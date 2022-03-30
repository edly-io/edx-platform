import React from 'react';
const MandatoryCoursesConfigTable = ({selectedSite, siteMandatoryCourses, editBtnClickHandler, resetBtnClickHandler}) => {

    const renderHeader = () => {
        let headerElement = ['#', 'course id', 'course name', 'allotted completion time', 'notification period', 'actions']
        return headerElement.map((key, index) => {
            return <th className={( key === 'actions')? 'actions': ''} key={index}>{key.toUpperCase()}</th>
        })
    }

    const renderBody = () => {
        let mandatory_courses_allotted_time = ""
        let mandatory_courses_notification_period = ""
        return siteMandatoryCourses.map(({id, course_id, course_name, course_config}) => {
            mandatory_courses_allotted_time = selectedSite.mandatory_courses_allotted_time
            mandatory_courses_notification_period = selectedSite.mandatory_courses_notification_period
            if (course_config){
                mandatory_courses_allotted_time = course_config.mandatory_courses_allotted_time
                mandatory_courses_notification_period = course_config.mandatory_courses_notification_period
            }

            return <tr key={id}>
                <td>{id}</td>
                <td>{course_id}</td>
                <td>{course_name}</td>
                <td>{mandatory_courses_allotted_time}</td>
                <td>{mandatory_courses_notification_period}</td>
                <td className='actions'>
                    <button
                        type="button"
                        data-toggle="modal"
                        data-target="#exampleModalCenter"
                        onClick={(event)=> editBtnClickHandler(false, course_id)}
                    >
                        <i className="fa fa-pencil" aria-hidden="true"></i>
                    </button>

                    <button
                        type="button"
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
            <h2>Mandatory Courses Due Date</h2>
            <div className='table-responsive'>
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
        </div>
    );
}
export default MandatoryCoursesConfigTable
