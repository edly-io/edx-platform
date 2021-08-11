define(['js/views/validation', 'codemirror', 'underscore', 'jquery', 'jquery.ui', 'js/utils/date_utils',
    'js/models/uploads', 'js/views/uploads', 'js/views/license', 'js/models/license',
    'common/js/components/views/feedback_notification', 'jquery.timepicker', 'date', 'gettext',
    'js/views/learning_info', 'js/views/instructor_info', 'edx-ui-toolkit/js/utils/string-utils'],
       function(ValidatingView, CodeMirror, _, $, ui, DateUtils, FileUploadModel,
                FileUploadDialog, LicenseView, LicenseModel, NotificationView,
                timepicker, date, gettext, LearningInfoView, InstructorInfoView, StringUtils) {
           var DetailsView = ValidatingView.extend({
    // Model class is CMS.Models.Settings.CourseDetails
               events: {
                   'input input': 'updateModel',
                   'input textarea': 'updateModel',
        // Leaving change in as fallback for older browsers
                   'change input': 'updateModel',
                   'change textarea': 'updateModel',
                   'change select': 'updateModel',
                   'click .remove-course-introduction-video': 'removeVideo',
                   'change #available-course-meta-tags': 'addMetaTagToSelectedList',
                   'focus #course-overview': 'codeMirrorize',
                   'focus #course-about-sidebar-html': 'codeMirrorize',
                   'mouseover .timezone': 'updateTime',
        // would love to move to a general superclass, but event hashes don't inherit in backbone :-(
                   'focus :input': 'inputFocus',
                   'blur :input': 'inputUnfocus',
                   'click .action-upload-image': 'uploadImage',
                   'click .add-course-learning-info': 'addLearningFields',
                   'click .add-course-instructor-info': 'addInstructorFields',
                   'click .add-course-credits-field': 'addCourseCreditsFields',
                   'click .remove-course-credits-field': 'removeCourseCreditsField',
                   'click .course-meta-tags-list-item': 'removeMetaTag',
               },

               initialize: function(options) {
                   options = options || {};
        // fill in fields
                   this.$el.find('#course-language').val(this.model.get('language'));
                   this.$el.find('#course-organization').val(this.model.get('org'));
                   this.$el.find('#course-number').val(this.model.get('course_id'));
                   this.$el.find('#course-name').val(this.model.get('run'));
                   this.$el.find('#course-site').val(this.model.get('course_site'));
                   this.$el.find('.set-date').datepicker({dateFormat: 'm/d/yy'});

        // Avoid showing broken image on mistyped/nonexistent image
                   this.$el.find('img').error(function() {
                       $(this).hide();
                   });
                   this.$el.find('img').load(function() {
                       $(this).show();
                   });

                   this.listenTo(this.model, 'invalid', this.handleValidationError);
                   this.listenTo(this.model, 'change', this.showNotificationBar);
                   this.selectorToField = _.invert(this.fieldToSelectorMap);
        // handle license separately, to avoid reimplementing view logic
                   this.licenseModel = new LicenseModel({asString: this.model.get('license')});
                   this.licenseView = new LicenseView({
                       model: this.licenseModel,
                       el: this.$('#course-license-selector').get(),
                       showPreview: true
                   });
                   this.listenTo(this.licenseModel, 'change', this.handleLicenseChange);

                   if (options.showMinGradeWarning || false) {
                       new NotificationView.Warning({
                           title: gettext('Course Credit Requirements'),
                           message: gettext('The minimum grade for course credit is not set.'),
                           closeIcon: true
                       }).show();
                   }

                   this.learning_info_view = new LearningInfoView({
                       el: $('.course-settings-learning-fields'),
                       model: this.model
                   });

                   this.instructor_info_view = new InstructorInfoView({
                       el: $('.course-instructor-details-fields'),
                       model: this.model
                   });
               },

               render: function() {
        // Clear any image preview timeouts set in this.updateImagePreview
                   clearTimeout(this.imageTimer);

                   DateUtils.setupDatePicker('start_date', this);
                   DateUtils.setupDatePicker('end_date', this);
                   DateUtils.setupDatePicker('certificate_available_date', this);
                   DateUtils.setupDatePicker('enrollment_start', this);
                   DateUtils.setupDatePicker('enrollment_end', this);
                   DateUtils.setupDatePicker('upgrade_deadline', this);

                   this.$el.find('#' + this.fieldToSelectorMap.overview).val(this.model.get('overview'));
                   this.codeMirrorize(null, $('#course-overview')[0]);

                   if (this.model.get('title') !== '') {
                       this.$el.find('#' + this.fieldToSelectorMap.title).val(this.model.get('title'));
                   } else {
                       var displayName = this.$el.find('#' + this.fieldToSelectorMap.title).attr('data-display-name');
                       this.$el.find('#' + this.fieldToSelectorMap.title).val(displayName);
                   }
                   this.$el.find('#' + this.fieldToSelectorMap.subtitle).val(this.model.get('subtitle'));
                   this.$el.find('#' + this.fieldToSelectorMap.duration).val(this.model.get('duration'));
                   this.$el.find('#' + this.fieldToSelectorMap.description).val(this.model.get('description'));

                   this.$el.find('#' + this.fieldToSelectorMap.short_description).val(this.model.get('short_description'));
                   this.$el.find('#' + this.fieldToSelectorMap.about_sidebar_html).val(
                       this.model.get('about_sidebar_html')
                   );
                   this.codeMirrorize(null, $('#course-about-sidebar-html')[0]);

                   this.$el.find('.current-course-introduction-video iframe').attr('src', this.model.videosourceSample());
                   this.$el.find('#' + this.fieldToSelectorMap.intro_video).val(this.model.get('intro_video') || '');
                   if (this.model.has('intro_video')) {
                       this.$el.find('.remove-course-introduction-video').show();
                   } else this.$el.find('.remove-course-introduction-video').hide();

                   this.$el.find('#' + this.fieldToSelectorMap.effort).val(this.model.get('effort'));

                   var courseImageURL = this.model.get('course_image_asset_path');
                   this.$el.find('#course-image-url').val(courseImageURL);
                   this.$el.find('#course-image').attr('src', courseImageURL);

                   var bannerImageURL = this.model.get('banner_image_asset_path');
                   this.$el.find('#banner-image-url').val(bannerImageURL);
                   this.$el.find('#banner-image').attr('src', bannerImageURL);

                   var videoThumbnailImageURL = this.model.get('video_thumbnail_image_asset_path');
                   this.$el.find('#video-thumbnail-image-url').val(videoThumbnailImageURL);
                   this.$el.find('#video-thumbnail-image').attr('src', videoThumbnailImageURL);

                   var pre_requisite_courses = this.model.get('pre_requisite_courses');
                   pre_requisite_courses = pre_requisite_courses.length > 0 ? pre_requisite_courses : '';
                   this.$el.find('#' + this.fieldToSelectorMap.pre_requisite_courses).val(pre_requisite_courses);

                   if (this.model.get('entrance_exam_enabled') == 'true') {
                       this.$('#' + this.fieldToSelectorMap.entrance_exam_enabled).attr('checked', this.model.get('entrance_exam_enabled'));
                       this.$('.div-grade-requirements').show();
                   } else {
                       this.$('#' + this.fieldToSelectorMap.entrance_exam_enabled).removeAttr('checked');
                       this.$('.div-grade-requirements').hide();
                   }
                   this.$('#' + this.fieldToSelectorMap.entrance_exam_minimum_score_pct).val(this.model.get('entrance_exam_minimum_score_pct'));

                   var selfPacedButton = this.$('#course-pace-self-paced'),
                       instructorPacedButton = this.$('#course-pace-instructor-paced'),
                       paceToggleTip = this.$('#course-pace-toggle-tip');
                   (this.model.get('self_paced') ? selfPacedButton : instructorPacedButton).attr('checked', true);

                   var eventTrueButton = this.$('#course-event-true'),
                       eventFalseButton = this.$('#course-event-false');
                   (this.model.get('is_event') ? eventTrueButton : eventFalseButton).attr('checked', true);

                   if (this.model.canTogglePace()) {
                       selfPacedButton.removeAttr('disabled');
                       instructorPacedButton.removeAttr('disabled');
                       paceToggleTip.text('');
                   } else {
                       selfPacedButton.attr('disabled', true);
                       instructorPacedButton.attr('disabled', true);
                       paceToggleTip.text(gettext('Course pacing cannot be changed once a course has started.'));
                   }

                   this.licenseView.render();
                   this.learning_info_view.render();
                   this.instructor_info_view.render();

                   return this;
               },
               fieldToSelectorMap: {
                   course_credits: 'credits-div',
                   language: 'course-language',
                   start_date: 'course-start',
                   end_date: 'course-end',
                   enrollment_start: 'enrollment-start',
                   enrollment_end: 'enrollment-end',
                   upgrade_deadline: 'upgrade-deadline',
                   certificate_available_date: 'certificate-available',
                   overview: 'course-overview',
                   title: 'course-title',
                   subtitle: 'course-subtitle',
                   duration: 'course-duration',
                   description: 'course-description',
                   about_sidebar_html: 'course-about-sidebar-html',
                   short_description: 'course-short-description',
                   intro_video: 'course-introduction-video',
                   effort: 'course-effort',
                   course_image_asset_path: 'course-image-url',
                   banner_image_asset_path: 'banner-image-url',
                   video_thumbnail_image_asset_path: 'video-thumbnail-image-url',
                   pre_requisite_courses: 'pre-requisite-course',
                   entrance_exam_enabled: 'entrance-exam-enabled',
                   entrance_exam_minimum_score_pct: 'entrance-exam-minimum-score-pct',
                   course_settings_learning_fields: 'course-settings-learning-fields',
                   add_course_learning_info: 'add-course-learning-info',
                   add_course_instructor_info: 'add-course-instructor-info',
                   course_learning_info: 'course-learning-info',
               },

               check_clearesult_credits_value_range_error: function()
               {
                    var is_value_range_error = false;
                    document.querySelectorAll('.group-settings .credit-value').forEach(function(credit){
                        if (credit.value == null || credit.value == "" || credit.value < 0.1 || credit.value > 10.0)
                        {
                            is_value_range_error = true;
                        }
                    });

                    if (is_value_range_error)
                    {
                        $("#clearesult-credit-error-range-value").show();
                    }
                    else
                    {
                        $("#clearesult-credit-error-range-value").hide();
                    }

                    return is_value_range_error;
               },

               check_clearesult_credits_no_provider_error: function()
               {
                    var available_course_credits = this.model.get('available_clearesult_providers');
                    if (available_course_credits.length)
                    {
                        $("#clearesult-credit-error-no-provider").hide();
                        return false;
                    }
                    else {
                        $("#clearesult-credit-error-no-provider").show();
                        return true;
                    }
               },

               check_clearesult_credits_error: function()
               {
                   if (this.check_clearesult_credits_value_range_error())
                   {
                       this.handleValidationError();
                       return true;
                   }
                   this.clearValidationErrors();
                   return false;

               },

               get_credit_dropdown_options: function() {
                    var select_list = '';
                    var available_course_credits = this.model.get('available_clearesult_providers');
                    available_course_credits.forEach((course_credit) => {
                        select_list += "<option value=\"" + course_credit.short_code + "\">" + course_credit.name + "</option>";
                    });
                    return select_list;
                },

               addCourseCreditsFields: function(event)
               {
                    this.showNotificationBar();
                    event.preventDefault();

                    var available_course_credits = this.model.get('available_clearesult_providers');
                    available_course_credits = this.model.get('available_clearesult_providers');
                    if (!this.check_clearesult_credits_no_provider_error() && !this.check_clearesult_credits_error() && available_course_credits.length)
                    {
                        document.querySelectorAll('.group-settings .credit-type').forEach(function(a){
                            a.disabled=true;
                        });

                        var new_credits_row = "<tr class=\"course-credit-row\"> \
                                <td class=\"credit-type-td\"> \
                                    <select class=\"credit-type\" name=\"credit-type\"> \
                            "
                            +
                            this.get_credit_dropdown_options()
                            +
                            "        </select> \
                                </td> \
                                <td class=\"credit-td\"> \
                                    <input type=\"number\" class=\"credit-value\" name=\"credit-value\" min=\"0.1\" max=\"10.0\" value=\"0.5\" step=\"0.1\"> \
                                </td> \
                                <td> \
                                    <button class=\"remove-course-credits-field\"> \
                                        <i class=\"fa fa-trash\" aria-hidden=\"true\"></i> \
                                    </button> \
                                </td> \
                            </tr> \
                        ";
                        var $tableBody = this.$el.find('tbody');
                        $tableBody.append(new_credits_row);
                    }
                    if (!this.check_clearesult_credits_error())
                    {
                        this.updateModel(event);
                    }
               },

               removeCourseCreditsField: function(event)
               {
                    event.preventDefault();
                    var table_row = event.target.closest ('tr');
                    var available_course_credits = this.model.get('available_clearesult_providers');
                    available_course_credits.push(
                        {
                            'name': table_row.getElementsByClassName('credit-type')[0].selectedOptions[0].text,
                            'short_code': table_row.getElementsByClassName('credit-type')[0].value
                        }
                    );
                    this.model.set('available_clearesult_providers', available_course_credits);
                    table_row.remove();

                    this.check_clearesult_credits_no_provider_error();

                    if (!this.check_clearesult_credits_error())
                    {
                        this.updateModel(event);
                    }
               },
               removeMetaTag: function(event) {
                this.model.get('associated_meta_tags').pop(event.target.textContent)
                event.target.remove();
                this.updateModel(event);
               },

                addMetaTagToSelectedList: function(event) {
                    if (event.target.value == ""
                    || (document.getElementById("associated-tag-" + event.target.value) != null
                    && document.getElementById("associated-tag-" + event.target.value) != undefined)) {
                        return;
                    }
                    var meta_tag_list = document.getElementById("course-meta-tags-list");
                    var meta_tag_list_item = document.createElement("li");
                    meta_tag_list_item.setAttribute("class", "course-meta-tags-list-item");
                    meta_tag_list_item.setAttribute("id", "associated-tag-" + event.target.value);
                    meta_tag_list_item.appendChild(document.createTextNode(event.target.value));
                    meta_tag_list.appendChild(meta_tag_list_item);
                    this.model.get("associated_meta_tags").push(event.target.value);
                },


               addLearningFields: function() {
        /*
        * Add new course learning fields.
        * */
                   var existingInfo = _.clone(this.model.get('learning_info'));
                   existingInfo.push('');
                   this.model.set('learning_info', existingInfo);
               },

               addInstructorFields: function() {
        /*
        * Add new course instructor fields.
        * */
                   var instructors = this.model.get('instructor_info').instructors.slice(0);
                   instructors.push({
                       name: '',
                       title: '',
                       organization: '',
                       image: '',
                       bio: ''
                   });
                   this.model.set('instructor_info', {instructors: instructors});
               },

               updateTime: function(e) {
                   var now = new Date(),
                       hours = now.getUTCHours(),
                       minutes = now.getUTCMinutes(),
                       currentTimeText = StringUtils.interpolate(
                gettext('{hours}:{minutes} (current UTC time)'),
                           {
                               hours: hours,
                               minutes: minutes
                           }
            );

                   $(e.currentTarget).attr('title', currentTimeText);
               },

               updateAvailableCreditProvidersList: function()
               {
                    var available_credits_providers = [];
                    var all_credit_providers = this.model.get('all_clearesult_providers');
                    var used_course_credits = this.model.get('course_credits');

                    all_credit_providers.forEach(function(provider) {
                        var is_found = false;
                        used_course_credits.forEach(function(used_credit) {
                            if (used_credit.credit_type_code == provider.short_code)
                            {
                                is_found = true;
                            }
                        });
                        if (!is_found){
                            available_credits_providers.push(provider);
                        }
                    });
                    this.model.set('available_clearesult_providers', available_credits_providers);
                },

               updateModelCourseCredits: function()
               {
                    var credit_rows = $('.course-credit-row');
                    if (credit_rows.length) {
                        var data = [];
                        $.each(credit_rows, function(index, form) {
                            data.push({
                                'credit_type_name': form.getElementsByClassName('credit-type')[0].selectedOptions[0].text,
                                'credit_type_code': form.getElementsByClassName('credit-type')[0].value,
                                'credits':form.getElementsByClassName('credit-value')[0].value
                            });
                        });
                        this.model.set('course_credits', data);
                    }
                    else
                    {
                        this.model.set('course_credits', []);
                    }
               },

               updateModel: function(event) {

                    if (!this.check_clearesult_credits_error())
                    {
                        this.updateModelCourseCredits();
                        this.updateAvailableCreditProvidersList();
                        this.showNotificationBar();
                    }

                   var value;
                   var index = event.currentTarget.getAttribute('data-index');
                   switch (event.currentTarget.id) {
                   case 'course-learning-info-' + index:
                       value = $(event.currentTarget).val();
                       var learningInfo = this.model.get('learning_info');
                       learningInfo[index] = value;
                       this.showNotificationBar();
                       break;
                   case 'course-instructor-name-' + index:
                   case 'course-instructor-title-' + index:
                   case 'course-instructor-organization-' + index:
                   case 'course-instructor-bio-' + index:
                       value = $(event.currentTarget).val();
                       var field = event.currentTarget.getAttribute('data-field'),
                           instructors = this.model.get('instructor_info').instructors.slice(0);
                       instructors[index][field] = value;
                       this.model.set('instructor_info', {instructors: instructors});
                       this.showNotificationBar();
                       break;
                   case 'course-instructor-image-' + index:
                       instructors = this.model.get('instructor_info').instructors.slice(0);
                       instructors[index].image = $(event.currentTarget).val();
                       this.model.set('instructor_info', {instructors: instructors});
                       this.showNotificationBar();
                       this.updateImagePreview(event.currentTarget, '#course-instructor-image-preview-' + index);
                       break;
                   case 'course-image-url':
                       this.updateImageField(event, 'course_image_name', '#course-image');
                       break;
                   case 'banner-image-url':
                       this.updateImageField(event, 'banner_image_name', '#banner-image');
                       break;
                   case 'video-thumbnail-image-url':
                       this.updateImageField(event, 'video_thumbnail_image_name', '#video-thumbnail-image');
                       break;
                   case 'entrance-exam-enabled':
                       if ($(event.currentTarget).is(':checked')) {
                           this.$('.div-grade-requirements').show();
                       } else {
                           this.$('.div-grade-requirements').hide();
                       }
                       this.setField(event);
                       break;
                   case 'entrance-exam-minimum-score-pct':
            // If the val is an empty string then update model with default value.
                       if ($(event.currentTarget).val() === '') {
                           this.model.set('entrance_exam_minimum_score_pct', this.model.defaults.entrance_exam_minimum_score_pct);
                       } else {
                           this.setField(event);
                       }
                       break;
                   case 'pre-requisite-course':
                       var value = $(event.currentTarget).val();
                       value = value == '' ? [] : [value];
                       this.model.set('pre_requisite_courses', value);
                       break;
        // Don't make the user reload the page to check the Youtube ID.
        // Wait for a second to load the video, avoiding egregious AJAX calls.
                   case 'course-introduction-video':
                       this.clearValidationErrors();
                       var previewsource = this.model.set_videosource($(event.currentTarget).val());
                       clearTimeout(this.videoTimer);
                       this.videoTimer = setTimeout(_.bind(function() {
                           this.$el.find('.current-course-introduction-video iframe').attr('src', previewsource);
                           if (this.model.has('intro_video')) {
                               this.$el.find('.remove-course-introduction-video').show();
                           } else {
                               this.$el.find('.remove-course-introduction-video').hide();
                           }
                       }, this), 1000);
                       break;
                   case 'course-pace-self-paced':
            // Fallthrough to handle both radio buttons
                   case 'course-pace-instructor-paced':
                       this.model.set('self_paced', JSON.parse(event.currentTarget.value));
                       break;

                    case 'course-event-false':
                    // Fallthrough to handle both radio buttons
                    case 'course-event-true':
                        this.model.set('is_event', JSON.parse(event.currentTarget.value));
                        break;

                   case 'course-language':
                   case 'course-effort':
                   case 'course-title':
                   case 'course-subtitle':
                   case 'course-duration':
                   case 'course-description':
                   case 'course-short-description':
                       this.setField(event);
                       break;
                   default: // Everything else is handled by datepickers and CodeMirror.
                       break;
                   }
               },
               updateImageField: function(event, image_field, selector) {
                   this.setField(event);
                   var url = $(event.currentTarget).val();
                   var image_name = _.last(url.split('/'));
        // If image path is entered directly, we need to strip the asset prefix
                   image_name = _.last(image_name.split('block@'));
                   this.model.set(image_field, image_name);
                   this.updateImagePreview(event.currentTarget, selector);
               },
               updateImagePreview: function(imagePathInputElement, previewSelector) {
        // Wait to set the image src until the user stops typing
                   clearTimeout(this.imageTimer);
                   this.imageTimer = setTimeout(function() {
                       $(previewSelector).attr('src', $(imagePathInputElement).val());
                   }, 1000);
               },
               removeVideo: function(event) {
                   event.preventDefault();
                   if (this.model.has('intro_video')) {
                       this.model.set_videosource(null);
                       this.$el.find('.current-course-introduction-video iframe').attr('src', '');
                       this.$el.find('#' + this.fieldToSelectorMap.intro_video).val('');
                       this.$el.find('.remove-course-introduction-video').hide();
                   }
               },
               codeMirrors: {},
               codeMirrorize: function(e, forcedTarget) {
                   var thisTarget, cachethis, field, cmTextArea;
                   if (forcedTarget) {
                       thisTarget = forcedTarget;
                       thisTarget.id = $(thisTarget).attr('id');
                   } else if (e !== null) {
                       thisTarget = e.currentTarget;
                   } else {
            // e and forcedTarget can be null so don't deference it
            // This is because in cases where we have a marketing site
            // we don't display the codeMirrors for editing the marketing
            // materials, except we do need to show the 'set course image'
            // workflow. So in this case e = forcedTarget = null.
                       return;
                   }

                   if (!this.codeMirrors[thisTarget.id]) {
                       cachethis = this;
                       field = this.selectorToField[thisTarget.id];
                       this.codeMirrors[thisTarget.id] = CodeMirror.fromTextArea(thisTarget, {
                           mode: 'text/html', lineNumbers: true, lineWrapping: true});
                       this.codeMirrors[thisTarget.id].on('change', function(mirror) {
                           mirror.save();
                           cachethis.clearValidationErrors();
                           var newVal = mirror.getValue();
                           if (cachethis.model.get(field) != newVal) {
                               cachethis.setAndValidate(field, newVal);
                           }
                       });
                       cmTextArea = this.codeMirrors[thisTarget.id].getInputField();
                       cmTextArea.setAttribute('id', thisTarget.id + '-cm-textarea');
                   }
               },

               revertView: function() {
        // Make sure that the CodeMirror instance has the correct
        // data from its corresponding textarea
                   var self = this;
                   this.model.fetch({
                       success: function() {
                           self.render();
                           _.each(self.codeMirrors, function(mirror) {
                               var ele = mirror.getTextArea();
                               var field = self.selectorToField[ele.id];
                               mirror.setValue(self.model.get(field));
                           });
                           self.licenseModel.setFromString(self.model.get('license'), {silent: true});
                           self.licenseView.render();
                       },
                       reset: true,
                       silent: true});
               },
               setAndValidate: function(attr, value) {
        // If we call model.set() with {validate: true}, model fields
        // will not be set if validation fails. This puts the UI and
        // the model in an inconsistent state, and causes us to not
        // see the right validation errors the next time validate() is
        // called on the model. So we set *without* validating, then
        // call validate ourselves.
                   this.model.set(attr, value);
                   this.model.isValid();
               },

               showNotificationBar: function() {
        // We always call showNotificationBar with the same args, just
        // delegate to superclass
                   ValidatingView.prototype.showNotificationBar.call(this,
                                                          this.save_message,
                                                          _.bind(this.saveView, this),
                                                          _.bind(this.revertView, this));
               },

               uploadImage: function(event) {
                   event.preventDefault();
                   var title = '',
                       selector = '',
                       image_key = '',
                       image_path_key = '';
                   switch (event.currentTarget.id) {
                   case 'upload-course-image':
                       title = gettext('Upload your course image.');
                       selector = '#course-image';
                       image_key = 'course_image_name';
                       image_path_key = 'course_image_asset_path';
                       break;
                   case 'upload-banner-image':
                       title = gettext('Upload your banner image.');
                       selector = '#banner-image';
                       image_key = 'banner_image_name';
                       image_path_key = 'banner_image_asset_path';
                       break;
                   case 'upload-video-thumbnail-image':
                       title = gettext('Upload your video thumbnail image.');
                       selector = '#video-thumbnail-image';
                       image_key = 'video_thumbnail_image_name';
                       image_path_key = 'video_thumbnail_image_asset_path';
                       break;
                   }

                   var upload = new FileUploadModel({
                       title: title,
                       message: gettext('Files must be in JPEG or PNG format.'),
                       mimeTypes: ['image/jpeg', 'image/png']
                   });
                   var self = this;
                   var modal = new FileUploadDialog({
                       model: upload,
                       onSuccess: function(response) {
                           var options = {};
                           options[image_key] = response.asset.display_name;
                           options[image_path_key] = response.asset.url;
                           self.model.set(options);
                           self.render();
                           $(selector).attr('src', self.model.get(image_path_key));
                       }
                   });
                   modal.show();
               },

               handleLicenseChange: function() {
                   this.showNotificationBar();
                   this.model.set('license', this.licenseModel.toString());
               }
           });

           return DetailsView;
       }); // end define()
