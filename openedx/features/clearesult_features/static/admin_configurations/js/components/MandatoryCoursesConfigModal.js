import React, { useEffect, useState } from "react";
import DualListBox from 'react-dual-listbox';
export default function MandatoryCoursesConfigModal({
    allottedTime,
    setAllottedTime,
    notificationTime,
    setNotificationTime,
    notificationTimeNormalCourses,
    setNotificationTimeNormalCourses,
    notificationTimeEventCourses,
    setNotificationTimeEventCourses,
    title,
    updateMandatoryCoursesConfig,
    isSiteConfig
}) {

    const btnText = (allottedTime == "" && notificationTime =="") ? "Add": "Update";

    const renderOtherCoursesConfigDiv = (isSiteConfig) => {
        if (isSiteConfig) {
            return (
                <div>
                    <h3>Other</h3>
                    <div className="form-group">
                        <label htmlFor="siteCourseNotificationTimeNormal">Course Notification period:</label>
                        <input type="number" className="form-control" value={notificationTimeNormalCourses} onChange={(event) => setNotificationTimeNormalCourses(event.target.value)} required id="siteCourseNotificationTimeNormal" aria-describedby="siteCourseNotificationTimeNormal" placeholder="Enter in Days"/>
                    </div>
                    <div className="form-group">
                        <label htmlFor="siteCourseNotificationTimeEvent">Event Notification period:</label>
                        <input type="number" className="form-control" value={notificationTimeEventCourses} onChange={(event) => setNotificationTimeEventCourses(event.target.value)} required id="siteCourseNotificationTimeEvent" aria-describedby="siteCourseNotificationTimeEvent" placeholder="Enter in Days"/>
                    </div>
                </div>
            )
        } else {
            return <div></div>
        }

    }

    return (
        <div>
            <div className="modal fade modal-update" id="exampleModalCenter" tabIndex="-1" role="dialog" aria-labelledby="exampleModalCenterTitle" aria-hidden="true">
                <div className="modal-dialog modal-dialog-centered" role="document">
                    <div className="modal-content">
                        <form onSubmit={(event)=>updateMandatoryCoursesConfig(event)}>
                            <div className="modal-header">
                                <span>Configurations of <strong>{title}</strong></span>
                                <button type="button" className="close" data-dismiss="modal" aria-label="Close">
                                <span aria-hidden="true">&times;</span>
                                </button>
                            </div>
                            <div className="modal-body">
                                <h3>Mandatory Courses</h3>
                                <div className="form-group">
                                    <label htmlFor="siteCourseAllottedTime">Time allotted to complete course:</label>
                                    <input type="number" className="form-control" value={allottedTime} onChange={(event) => setAllottedTime(event.target.value)} required id="siteCourseAllottedTime" aria-describedby="siteCourseAllottedTimeHelp" placeholder="Enter in Days" />
                                </div>
                                <div className="form-group">
                                    <label htmlFor="siteCourseNotificationTime">Course Notification period:</label>
                                    <input type="number" className="form-control" value={notificationTime} onChange={(event) => setNotificationTime(event.target.value)} required id="siteCourseNotificationTime" aria-describedby="siteCourseNotificationTime" placeholder="Enter in Days"/>
                                </div>
                                {renderOtherCoursesConfigDiv(isSiteConfig)}
                            </div>
                            <div className="modal-footer">
                                <button type="button" className="btn btn-secondary" data-dismiss="modal">Close</button>
                                <button type="submit" className="btn btn-primary">{btnText}</button>
                            </div>
                    	</form>
                	</div>
                </div>
            </div>
        </div>
    );
}
