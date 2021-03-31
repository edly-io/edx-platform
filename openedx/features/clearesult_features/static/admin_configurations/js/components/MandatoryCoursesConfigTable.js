import React from 'react';
const MandatoryCoursesConfigTable = ({selectedSite, siteMandatoryCourses, editBtnClickHandler, resetBtnClickHandler}) => {

    const renderHeader = () => {
        let headerElement = ['#', 'course id', 'course name', 'alotted completion time', 'notification period', 'Action']
        return headerElement.map((key, index) => {
            return <th key={index}>{key.toUpperCase()}</th>
        })
    }

    const renderBody = () => {
        let mandatory_courses_alotted_time = ""
        let mandatory_courses_notification_period = ""
        return siteMandatoryCourses.map(({id, course_id, course_name, course_config}) => {
            mandatory_courses_alotted_time = selectedSite.mandatory_courses_alotted_time
            mandatory_courses_notification_period = selectedSite.mandatory_courses_notification_period
            if (course_config){
                mandatory_courses_alotted_time = course_config.mandatory_courses_alotted_time
                mandatory_courses_notification_period = course_config.mandatory_courses_notification_period
            }

            return <tr key={id}>
                <td>{id}</td>
                <td>{course_id}</td>
                <td>{course_name}</td>
                <td>{mandatory_courses_alotted_time}</td>
                <td>{mandatory_courses_notification_period}</td>
                <td>
                    <button
                        type="button"
                        className="btn btn-primary"
                        data-toggle="modal"
                        data-target="#exampleModalCenter"
                        onClick={(event)=> editBtnClickHandler(false, course_id)}
                    >
                        <i className="fa fa-pencil" aria-hidden="true"></i>
                    </button>

                    <button
                        type="button"
                        className="btn btn-primary"
                        onClick={(event) => {if (window.confirm('Are you sure you want to reset?')) resetBtnClickHandler(course_id)}}
                        value={course_id}
                    >
                        <i className="fa fa-refresh" aria-hidden="true"></i>
                    </button>
                </td>

            </tr>
        })
    }

    return (
        <div>
            <h2>Mandatory Courses Specific Due Dates</h2>
            <table id='catalogs' className="table">
                <thead>
                    <tr>
                        {renderHeader()}
                    </tr>
                </thead>
                <tbody>
                    {renderBody()}
                </tbody>
            </table>
        </div>
    );
}
export default MandatoryCoursesConfigTable
