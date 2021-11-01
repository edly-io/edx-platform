""" View for interacting with the payment processor. """

from __future__ import unicode_literals

import base64
import hashlib
import hmac
import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from oscar.apps.partner import strategy
from oscar.core.loading import get_class, get_model
from rest_framework.views import APIView

from ecommerce.core.url_utils import get_lms_dashboard_url
from ecommerce.extensions.checkout.mixins import EdxOrderPlacementMixin
from ecommerce.extensions.payment.exceptions import InvalidBasketError
from ecommerce.extensions.payment.processors.authorizenet import AuthorizeNet
from ecommerce.notifications.notifications import send_notification

logger = logging.getLogger(__name__)

Applicator = get_class('offer.applicator', 'Applicator')
Basket = get_model('basket', 'Basket')
BillingAddress = get_model('order', 'BillingAddress')
Country = get_model('address', 'Country')
NoShippingRequired = get_class('shipping.methods', 'NoShippingRequired')
OrderNumberGenerator = get_class('order.utils', 'OrderNumberGenerator')
OrderTotalCalculator = get_class('checkout.calculators', 'OrderTotalCalculator')
PaymentProcessorResponse = get_model('payment', 'PaymentProcessorResponse')

NOTIFICATION_TYPE_AUTH_CAPTURE_CREATED = "net.authorize.payment.authcapture.created"


class AuthorizeNetNotificationView(EdxOrderPlacementMixin, APIView):
    """
        Execute an approved AuthorizeNet payment and place an order for paid products.
    """

    @property
    def payment_processor(self):
        return AuthorizeNet(self.request.site)

    # Disable atomicity for the view. Otherwise, we'd be unable to commit to the database
    # until the request had concluded; Django will refuse to commit when an atomic() block
    # is active, since that would break atomicity. Without an order present in the database
    # at the time fulfillment is attempted, asynchronous order fulfillment tasks will fail.
    @method_decorator(transaction.non_atomic_requests)
    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        return super(AuthorizeNetNotificationView, self).dispatch(request, *args, **kwargs)

    def send_transaction_declined_email(self, basket, transaction_status, course_title):
        """
            send email to the user after receiving a transcation notification with
            decilened/error status.
            Arguments:
                basket: transaction relevant basket.
                transaction_status: Error or Declined.
                course_title: course for which transaction was performed.
        """
        send_notification(
            basket.owner,
            'TRANSACTION_REJECTED',
            {
                'course_title': course_title,
                'transaction_status': transaction_status,
            },
            basket.site
        )

    def get_basket(self, basket_id):
        """
            Retrieve a basket using a basket Id.
            Arguments:
                payment_id: payment_id received from AuthorizeNet.
            Returns:
                It will return related basket
        """
        if not basket_id:
            return None

        try:
            basket = Basket.objects.get(id=basket_id)
            basket.strategy = strategy.Default()
            return basket
        except (ValueError, ObjectDoesNotExist):
            return None

    def get_billing_address(self, transaction_bill, order_number, basket):
        """
            Prepare and return a billing address object using transaction billing information.
            Arguments:
                transaction_bill: bill information from AuthorizeNet transaction response.
                order_number: related order number
            Returns:
                It will return billing object
        """
        try:
            billing_address = BillingAddress(
                first_name=str(getattr(transaction_bill, 'firstName', '')),
                last_name=str(getattr(transaction_bill, 'lastName', '')),
                line1=str(getattr(transaction_bill, 'address', '')),
                line4=str(getattr(transaction_bill, 'city', '')),  # Oscar uses line4 for city
                state=str(getattr(transaction_bill, 'state', '')),
                country=Country.objects.get(
                    iso_3166_1_a2__iexact=transaction_bill.country
                ),
                postcode=str(getattr(transaction_bill, 'zip', ''))
            )

        except Exception:  # pylint: disable=broad-except
            exception_msg = (
                'An error occurred while parsing the billing address for basket [%d]. '
                'No billing address will be stored for the resulting order [%s].'
            )
            logger.exception(exception_msg, basket.id, order_number)
            billing_address = None
        return billing_address

    def call_handle_order_placement(self, basket, request, transaction_details):
        """
            Handle order placement for approved transactions.
        """
        try:
            shipping_method = NoShippingRequired()
            shipping_charge = shipping_method.calculate(basket)
            order_total = OrderTotalCalculator().calculate(basket, shipping_charge)

            user = basket.owner
            order_number = str(transaction_details.transaction.order.invoiceNumber)

            billing_address = self.get_billing_address(
                transaction_details.transaction.billTo, order_number, basket)

            order = self.handle_order_placement(
                order_number=order_number,
                user=user,
                basket=basket,
                shipping_address=None,
                shipping_method=shipping_method,
                shipping_charge=shipping_charge,
                billing_address=billing_address,
                order_total=order_total,
                request=request
            )
            self.handle_post_order(order)

        except Exception:  # pylint: disable=broad-except
            self.log_order_placement_exception(basket.order_number, basket.id)

    def post(self, request):
        """
            This view will be called by AuthorizeNet to handle notifications and order placement.
            It should return 200 (to Authorizenet) after receiving a notification so they'll know
            that notification has been received at our end otherwise they will send it again and
            again after the particular interval.
        """

        if request.headers.get('X-Anet-Signature'):
            client_signature_key = self.payment_processor.signature_key
            request_signature = request.headers.get('X-Anet-Signature')[7:].lower()
            hmac_data = hmac.new(client_signature_key.encode(), request.body, hashlib.sha512)
            signature_generated = hmac_data.hexdigest()
            if signature_generated != request_signature:
                logger.error(
                    'UnAuthorized Request, HMAC-SHA512 generated from Client Signature Key and request Body didn\'t matched with request Signature Key',
                )
                return HttpResponse(status=401)
        else:
            logger.error(
                'Request Didn\'t contains X-Anet-Signature in header',
            )
            return HttpResponse(status=400)

        notification = request.data
        if notification.get("eventType") != NOTIFICATION_TYPE_AUTH_CAPTURE_CREATED:
            error_meassage = (
                'Received AuthroizeNet notifciation with event_type [%s]. Currently, '
                'We are not handling such type of notifications.'
            )
            logger.error(error_meassage, notification.get("eventType"))

            return HttpResponse(status=204)

        notification_id = notification.get("notificationId")
        payload = notification.get("payload", {})

        transaction_id = payload.get("id")
        if not transaction_id:
            logger.error(
                'Recieved AuthorizeNet transaction notification without transaction_id',
            )
            return HttpResponse(status=400)

        try:
            transaction_details = self.payment_processor.get_transaction_detail(transaction_id)

            order_number = str(transaction_details.transaction.order.invoiceNumber)
            basket_id = OrderNumberGenerator().basket_id(order_number)

            logger.info(
                'Received AuthorizeNet payment notification for transaction [%s], associated with basket [%d].',
                transaction_id,
                basket_id
            )

            basket = self.get_basket(basket_id)

            if not basket:
                logger.error('Received AuthorizeNet payment notification for non-existent basket [%s].', basket_id)
                raise InvalidBasketError

            if basket.status != Basket.FROZEN:
                logger.info(
                    'Received AuthorizeNet payment notification for basket [%d] which is in a non-frozen state, [%s]',
                    basket.id, basket.status
                )

            self.payment_processor.record_processor_response(
                notification, transaction_id=notification_id, basket=basket
            )

            product = basket.all_lines()[0].product
            if payload.get("responseCode") != 1:
                transaction_status = "Declined" if payload.get("responseCode") == 2 else "Error"
                error_message = (
                    'AuthorizeNet transaction of transaction_id [%s] associated with basket [%s] has '
                    'been rejected with status: [%s].'
                )
                logger.error(error_message, transaction_id, basket_id, transaction_status)
                course_title = product.course.name
                self.send_transaction_declined_email(basket, transaction_status, course_title)

            else:
                with transaction.atomic():
                    self.handle_payment(transaction_details, basket)
                    self.call_handle_order_placement(basket, request, transaction_details)

        except Exception:  # pylint: disable=broad-except
            logger.exception(
                'An error occurred while processing the AuthorizeNet payment for transaction_id [%s].',
                transaction_id
            )
        return HttpResponse(status=200)


def handle_redirection(request):
    """
        Handle AuthorizeNet redirection. This view will be called when a user clicks on continue button
        from AuthorizeNet receipt page. It will handle Transaction cookie named as "pendingTransactionCourse".
        Transaction cookie should contain encrypted course id for which transaction has been performed butq
        notification is yet to be received. This cookie will be used at LMS-side to display waiting
        alert to the user.
    """
    domain = settings.ECOMMERCE_COOKIE_DOMAIN
    lms_dashboard = 'http://localhost:18000/dashboard'  # get_lms_dashboard_url()'
    response = redirect(lms_dashboard)
    basket_id = request.GET.get('basket')
    course_id_hash = None
    basket = None

    try:
        basket = Basket.objects.get(id=basket_id)
    except Basket.DoesNotExist:
        logger.error('Basket with ID: %d not found, cannot generete GA event.', basket_id)
    else:
        # try:
        #     ga_event = _get_ga_event(request, basket)
        # except Exception as ex:  # pylint: disable=broad-except
        #     logger.exception('Error while trying to get GA event data: %s', str(ex))
        # else:
        #     add_to_ga_events_cookie(request, response, 'purchase', ga_event, domain=domain)
        # print(basket)
        course_id = _get_course_id_from_basket(basket)
        course_id_hash = base64.b64encode(course_id.encode("utf-8")) if course_id else ''

    if course_id_hash:
        response.set_cookie('pendingTransactionCourse', course_id_hash, domain=domain)

    return response


def _get_ga_event(request, basket):
    """
    Generates dict containing data for Google Analytics "purchase" event
    Arguments:
        request: Request object
        basket: Basket object
    Returns:
        A dict cotaining data for Google Analytics event
    """
    basket.strategy = strategy.Default()

    ga_event = {
        'transaction_id': basket.order_number,
        'affiliation': request.site.name,
        'currency': basket.currency,
        'tax': float(basket.total_tax),
        'shipping': 0,
        'items': []
    }

    total_price = 0

    for basket_line_item in basket.all_lines():  # pylint: disable=protected-access
        price = float(basket_line_item.line_price_incl_tax_incl_discounts)
        total_price += price
        item = {
            'id': basket_line_item.product.stockrecords.all()[0].partner_sku,
            'name': basket_line_item.product.course.name,
            'brand': basket_line_item.product.course.partner.__str__(),
            'category': '',
            'variant': '',
            'quantity': basket_line_item.quantity,
            'price': price
        }
        ga_event['items'].append(item)

    ga_event['value'] = total_price

    return ga_event


def _get_course_id_from_basket(basket):
    """
    Gets the course ID from the basket
    Arguments:
        basket: Basket object
    Returns:
        course_id if found in the basket, otherwise empty string
    """
    line_items = basket.all_lines()  # pylint: disable=protected-access
    if line_items:
        try:
            return line_items[0].product.course_id
        except (AttributeError, IndexError):
            logger.exception('Basket %s has no line items. Could get course ID from the basket', basket)
    return None
