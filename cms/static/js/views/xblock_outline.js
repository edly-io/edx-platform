/**
 * The XBlockOutlineView is used to render an xblock and its children based upon the information
 * provided in the XBlockInfo model. It is a recursive set of views where each XBlock has its own instance.
 *
 * The class provides several opportunities to override the default behavior in subclasses:
 *  - shouldRenderChildren defaults to true meaning that the view should also create child views
 *  - shouldExpandChildren defaults to true meaning that the view should show itself as expanded
 *  - refresh is called when a server change has been made and the view needs to be refreshed
 *
 * The view can be constructed with an initialState option which is a JSON structure representing
 * the desired initial state. The parameters are as follows:
 *  - locator_to_show - the locator for the xblock which is the one being explicitly shown
 *  - scroll_offset - the scroll offset to use for the locator being shown
 *  - edit_display_name - true if the shown xblock's display name should be in inline edit mode
 */
define(['jquery', 'underscore', 'gettext', 'js/views/baseview', 'common/js/components/utils/view_utils',
    'js/views/utils/xblock_utils', 'js/views/xblock_string_field_editor',
    'edx-ui-toolkit/js/utils/string-utils', 'edx-ui-toolkit/js/utils/html-utils'],
    function($, _, gettext, BaseView, ViewUtils, XBlockViewUtils, XBlockStringFieldEditor, StringUtils, HtmlUtils) {
        'use strict';
        var XBlockOutlineView = BaseView.extend({
            // takes XBlockInfo as a model

            options: {
                collapsedClass: 'is-collapsed'
            },

            templateName: 'xblock-outline',

            initialize: function() {
                BaseView.prototype.initialize.call(this);
                this.initialState = this.options.initialState;
                this.expandedLocators = this.options.expandedLocators;
                this.template = this.options.template;
                if (!this.template) {
                    this.template = this.loadTemplate(this.templateName);
                }
                this.parentInfo = this.options.parentInfo;
                this.parentView = this.options.parentView;
                this.renderedChildren = false;
                this.model.on('sync', this.onSync, this);
            },

            render: function() {
                this.renderTemplate();
                this.addButtonActions(this.$el);
                this.addNameEditor();

                // For cases in which we need to suppress the header controls during rendering, we'll
                // need to add the current model's id/locator to the set of expanded locators
                if (this.model.get('is_header_visible') !== null && !this.model.get('is_header_visible')) {
                    var locator = this.model.get('id');
                    if (!_.isUndefined(this.expandedLocators) && !this.expandedLocators.contains(locator)) {
                        this.expandedLocators.add(locator);
                        this.refresh();
                    }
                }

                if (this.shouldRenderChildren() && this.shouldExpandChildren()) {
                    this.renderChildren();
                } else {
                    this.renderedChildren = false;
                }
                return this;
            },

            renderTemplate: function() {
                var html = this.template(this.getTemplateContext());
                if (this.parentInfo) {
                    this.setElement($(html));
                } else {
                    HtmlUtils.setHtml(
                        this.$el,
                        HtmlUtils.HTML(html)
                    );
                }
            },

            getTemplateContext: function() {
                var xblockInfo = this.model,
                    childInfo = xblockInfo.get('child_info'),
                    parentInfo = this.parentInfo,
                    xblockType = XBlockViewUtils.getXBlockType(this.model.get('category'), this.parentInfo),
                    xblockTypeDisplayName = XBlockViewUtils.getXBlockType(this.model.get('category'), this.parentInfo, true),
                    parentType = parentInfo ? XBlockViewUtils.getXBlockType(parentInfo.get('category')) : null,
                    addChildName = null,
                    defaultNewChildName = null,
                    isCollapsed = this.shouldRenderChildren() && !this.shouldExpandChildren();
                if (childInfo) {
                    addChildName = StringUtils.interpolate(gettext('New {component_type}'), {
                        component_type: childInfo.display_name
                    }, true);
                    defaultNewChildName = childInfo.display_name;
                }
                /* globals course */
                return {
                    xblockInfo: xblockInfo,
                    visibilityClass: XBlockViewUtils.getXBlockVisibilityClass(xblockInfo.get('visibility_state')),
                    typeListClass: XBlockViewUtils.getXBlockListTypeClass(xblockType),
                    parentInfo: this.parentInfo,
                    xblockType: xblockType,
                    xblockTypeDisplayName: xblockTypeDisplayName,
                    parentType: parentType,
                    childType: childInfo ? XBlockViewUtils.getXBlockType(childInfo.category, xblockInfo) : null,
                    childCategory: childInfo ? childInfo.category : null,
                    addChildLabel: addChildName,
                    defaultNewChildName: defaultNewChildName,
                    isCollapsed: isCollapsed,
                    includesChildren: this.shouldRenderChildren(),
                    hasExplicitStaffLock: this.model.get('has_explicit_staff_lock'),
                    staffOnlyMessage: this.model.get('staff_only_message'),
                    course: course
                };
            },

            renderChildren: function() {
                var self = this,
                    parentInfo = this.model;
                if (parentInfo.get('child_info')) {
                    _.each(this.model.get('child_info').children, function(childInfo) {
                        var childOutlineView = self.createChildView(childInfo, parentInfo);
                        childOutlineView.render();
                        self.addChildView(childOutlineView);
                    });
                }
                this.renderedChildren = true;
            },

            getListElement: function() {
                return this.$('> .outline-content > ol');
            },

            addChildView: function(childView, xblockElement) {
                if (xblockElement) {
                    childView.$el.insertAfter(xblockElement);
                } else {
                    this.getListElement().append(childView.$el);
                }
            },

            addNameEditor: function() {
                var self = this,
                    xblockField = this.$('.wrapper-xblock-field'),
                    XBlockOutlineFieldEditor, nameEditor;
                if (xblockField.length > 0) {
                    // Make a subclass of the standard xblock string field editor which refreshes
                    // the entire section that this view is contained in. This is necessary as
                    // changing the name could have caused the section to change state.
                    XBlockOutlineFieldEditor = XBlockStringFieldEditor.extend({
                        refresh: function() {
                            self.refresh();
                        }
                    });
                    nameEditor = new XBlockOutlineFieldEditor({
                        el: xblockField,
                        model: this.model
                    });
                    nameEditor.render();
                }
            },

            toggleExpandCollapse: function(event) {
                // The course outline page tracks expanded locators. The unit location sidebar does not.
                if (this.expandedLocators) {
                    var locator = this.model.get('id');
                    var wasExpanded = this.expandedLocators.contains(locator);
                    if (wasExpanded) {
                        this.expandedLocators.remove(locator);
                    } else {
                        this.expandedLocators.add(locator);
                    }
                }
                // Ensure that the children have been rendered before expanding
                this.ensureChildrenRendered();
                BaseView.prototype.toggleExpandCollapse.call(this, event);
            },

            /**
             * Verifies that the children are rendered (if they should be).
             */
            ensureChildrenRendered: function() {
                if (!this.renderedChildren && this.shouldRenderChildren()) {
                    this.renderChildren();
                }
            },

            /**
             * Adds handlers to the each button in the header's panel. This is managed outside of
             * Backbone's own event registration so that the handlers don't get scoped to all the
             * children of this view.
             * @param element The root element of this view.
             */
            addButtonActions: function(element) {
                var self = this;
                element.find('.delete-button').click(_.bind(this.handleDeleteEvent, this));
                element.find('.duplicate-button').click(_.bind(this.handleDuplicateEvent, this));
                element.find('.button-new').click(_.bind(this.handleAddEvent, this));
            },

            shouldRenderChildren: function() {
                return true;
            },

            shouldExpandChildren: function() {
                return true;
            },

            getChildViewClass: function() {
                return XBlockOutlineView;
            },

            createChildView: function(childInfo, parentInfo, options) {
                var viewClass = this.getChildViewClass();
                return new viewClass(_.extend({
                    model: childInfo,
                    parentInfo: parentInfo,
                    parentView: this,
                    initialState: this.initialState,
                    expandedLocators: this.expandedLocators,
                    template: this.template
                }, options));
            },

            onSync: function(event) {
                var hasChangedAttributes = ViewUtils.hasChangedAttributes(
                    this.model,
                    ['visibility_state', 'child_info', 'display_name', 'highlights']
                );
                if (hasChangedAttributes) {
                    this.onXBlockChange();
                }
            },

            onXBlockChange: function() {
                var oldElement = this.$el,
                    viewState = this.initialState;
                this.render();
                if (this.parentInfo) {
                    oldElement.replaceWith(this.$el);
                }
                if (viewState) {
                    this.setViewState(viewState);
                }
            },

            setViewState: function(viewState) {
                var locatorToShow = viewState.locator_to_show,
                    scrollOffset = viewState.scroll_offset || 0,
                    editDisplayName = viewState.edit_display_name,
                    locatorElement;
                if (locatorToShow) {
                    if (locatorToShow === this.model.id) {
                        locatorElement = this.$el;
                    } else {
                        locatorElement = this.$('.outline-item[data-locator="' + locatorToShow + '"]');
                    }
                    if (locatorElement.length > 0) {
                        ViewUtils.setScrollOffset(locatorElement, scrollOffset);
                    } else {
                        console.error('Failed to show item with locator ' + locatorToShow + '');
                    }
                    if (editDisplayName) {
                        locatorElement.find('> div[class$="header"] .xblock-field-value-edit').click();
                    }
                }
                this.initialState = null;
            },

            /**
             * Refresh the view's model from the server, which will cause the view to refresh.
             * @returns {jQuery promise} A promise representing the refresh operation.
             */
            refresh: function() {
                return this.model.fetch();
            },

            onChildAdded: function(locator, category) {
                // For units, redirect to the new page, and for everything else just refresh inline.
                if (category === 'vertical') {
                    this.onUnitAdded(locator);
                } else {
                    this.refresh();
                }
            },

            onUnitAdded: function(locator) {
                ViewUtils.redirect('/container/' + locator + '?action=new');
            },

            onChildDeleted: function() {
                this.refresh();
            },

            handleButtonEnable: function(deltaCount){
                const btn  = $('.action-export .btn');
                if(btn.prop("disabled")){
                    btn.prop("disabled", false);
                    btn.removeClass('catch-up');
                }
                if(deltaCount===0){
                    btn.addClass("catch-up");
                    btn.get(0).lastChild.nodeValue = "All Caught Up";
                    $("#course_unsync_value").text("\uD83C\uDF89");
                    btn.prop("disabled", true);  
                }else{
                    btn.get(0).lastChild.nodeValue = "Sync Course Data";
                    $("#course_unsync_value").text(deltaCount);
                }
            },

            handleDeleteDeltaCount: function(model, parentView){
                let delta = JSON.parse(localStorage.getItem('CourseDelta'));
                const span = $("#course_unsync_value");
                const category = model.get('category'); 
                if(category==='chapter'){
                    const findItem = delta.section.find(item => item.id === model.id);
                    if(findItem){
                        if(findItem.modified_or_not_exist){
                            delta.delta_count = Number(delta.delta_count)-1;
                            span.text(delta.delta_count);
                        }else{
                            delta.delta_count = Number(delta.delta_count)+1;
                            span.text(delta.delta_count);
                        }
                        this.handleButtonEnable(delta.delta_count);
                        delta.section = delta.section.filter(item =>  item.id !== model.id);
                        this.handleProgressBar(delta);
                        localStorage.setItem("CourseDelta", JSON.stringify(delta));
                    }
                }else if(category==='sequential'){ 
                    const findItem = delta.section.find(item => item.id === parentView.model.id);
                    if(findItem){
                        if(!findItem.modified_or_not_exist){
                            delta.delta_count = Number(delta.delta_count)+1;
                            span.text(delta.delta_count);
                            this.handleButtonEnable(delta.delta_count);
                            this.handleProgressBar(delta);
                            localStorage.setItem("CourseDelta", JSON.stringify(delta));
                        }
                    }
                }
            },

            handleDeleteEvent: function(event) {
                var self = this,
                    parentView = this.parentView,
                    xblockType = XBlockViewUtils.getXBlockType(this.model.get('category'), parentView.model, true);
                event.preventDefault();
                XBlockViewUtils.deleteXBlock(this.model, xblockType).done(function() {
                    try{
                        self.handleDeleteDeltaCount(self.model, self.parentView);
                    }catch(err){}
                    if (parentView){
                        parentView.onChildDeleted(self, event);
                    }
                });
            },

            /**
             * Finds appropriate parent element for an xblock element.
             * @param {jquery Element}  xblockElement  The xblock element to be duplicated.
             * @param {String}  xblockType The front-end terminology of the xblock category.
             * @returns {jquery Element} Appropriate parent element of xblock element.
             */
            getParentElement: function(xblockElement, xblockType) {
                var xblockMap = {
                        unit: 'subsection',
                        subsection: 'section',
                        section: 'course'
                    },
                    parentXblockType = xblockMap[xblockType];
                return xblockElement.closest('.outline-' + parentXblockType);
            },

            /**
             * Duplicate event handler.
             */
            handleDuplicateEvent: function(event) {
                var self = this,
                    xblockType = XBlockViewUtils.getXBlockType(self.model.get('category'), self.parentView.model),
                    xblockElement = $(event.currentTarget).closest('.outline-item'),
                    parentElement = self.getParentElement(xblockElement, xblockType);

                event.preventDefault();
                XBlockViewUtils.duplicateXBlock(xblockElement, parentElement)
                    .done(function(data) {
                        if (self.parentView) {
                            try{
                                self.handleUpdateDeltaCount(
                                    self.model.get('category'),
                                    '',
                                    data.locator,
                                    self.parentView.model.id
                                );
                            }catch(err){}
                            self.parentView.onChildDuplicated(
                                data.locator,
                                xblockType,
                                xblockElement
                            );
                        }else{
                            try{
                                self.handleUpdateDeltaCount(
                                    self.model.get('category'),
                                    '',
                                    data.locator,
                                    ''
                                );
                            }catch(err){}
                        }
                    });
            },

            handleProgressBar: function(delta){
                const section_length =  delta.section.length;
                if (section_length !== 0){
                    const percent = ((section_length - delta.delta_count) / section_length) * 100;
                    $('.progress-bar span').css('width', percent + '%');
                }
            },

            handleUpdateDeltaCount: function(category, name='', id='', parent_id=''){
                let delta = JSON.parse(localStorage.getItem('CourseDelta'));
                const span = $("#course_unsync_value");
                if(category==='chapter'){
                    delta.delta_count=Number(delta.delta_count)+1;
                    span.text(delta.delta_count);
                    delta.section.push({
                        name:name, 
                        modified_or_not_exist:true, 
                        id:id, 
                    });
                    this.handleButtonEnable(delta.delta_count);
                    this.handleProgressBar(delta);
                    localStorage.setItem("CourseDelta", JSON.stringify(delta));
                }
                else if(category==='sequential'){
                    const findItem = delta.section.find(item => item.id === parent_id);
                    if(findItem && !findItem.modified_or_not_exist){
                        findItem.modified_or_not_exist= true; 
                        delta.delta_count=Number(delta.delta_count)+1;
                        span.text(delta.delta_count);
                        this.handleButtonEnable(delta.delta_count);
                        this.handleProgressBar(delta);
                        localStorage.setItem("CourseDelta", JSON.stringify(delta));
                    }
                }
            },

            handleAddEvent: function(event) {
                var self = this,
                    $target = $(event.currentTarget),
                    category = $target.data('category');
                event.preventDefault();
                XBlockViewUtils.addXBlock($target).done(function(locator) {
                    try{
                        self.handleUpdateDeltaCount(category, $target.data('default-name'), locator, $target.data('parent'));
                    }catch(err){}
                    self.onChildAdded(locator, category, event);
                });
            }
        });

        return XBlockOutlineView;
    }); // end define();
