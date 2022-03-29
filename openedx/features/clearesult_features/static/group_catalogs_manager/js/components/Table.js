import React, { useState, useEffect } from 'react';
import ToggleSwitch from '../components/ToggleSwitch';


const Table = ({
    groupCatalogs,
    removeCatalog,
    updateMandatoryCourses
  }) => {
    const renderHeader = () => {
        let headerElement = ['#', 'id', 'group', 'site']

        return headerElement.map((key, index) => {
            return <th key={index}>{key.toUpperCase()}</th>
        })
    }

    const renderBody = () => {
        if (!groupCatalogs || !groupCatalogs.length){
            return <tr></tr>
        }

        return groupCatalogs.map(({ id, name, site, catalogs}) => {
            const div_id = "#collapse" + id
            const div_id2 = "collapse" + id
            let group_id = id

            return (
            <React.Fragment key={id}>
                <tr>
                    <td>
                        <a className="row-opener collapsed" data-toggle="collapse" href={div_id} role="button" aria-expanded="false" aria-controls={div_id2}>
                          <i className="fa fa-chevron-right" aria-hidden="true"></i>
                        </a>
                    </td>
                    <td>{id} {(id != site.default_group) ? '': <span className="badge badge-info">Default</span>}</td>
                    <td>{name}</td>
                    <td>{site.domain}</td>
                </tr>
                <tr className="slide-row">
                    <td colSpan="4">
                        <div id={div_id2} className="collapse in">
                            <div className="slide">
                                <table className="table">
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            <th>CATALOG</th>
                                            <th>SITE</th>
                                            <th className="actions">ACTIONS</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                    { catalogs.map(({id, catalog, mandatory_courses}) => {
                                        const div_id_1 = "#test" + id
                                        const div_id_2 = "test" + id
                                        let mandatory_table_id = id
                                        return (
                                            <React.Fragment key={id}>
                                                <tr key={id}>
                                                    <td>
                                                        <a className="row-opener collapsed" data-toggle="collapse" href={div_id_1} role="button" aria-expanded="false" aria-controls={div_id_2}>
                                                            <i className="fa fa-chevron-right" aria-hidden="true"></i>
                                                        </a>
                                                    </td>
                                                    <td>{catalog.name}</td>
                                                    <td>{catalog.site!=null ? catalog.site.domain: "public"}</td>
                                                    <td className="actions">
                                                        <button
                                                            value={group_id + ',' + catalog.id}
                                                            onClick={(e) => {if (window.confirm('Are you sure you wish to remove this catalog?')) removeCatalog(group_id + ',' + catalog.id)}}
                                                        >
                                                            <i className="fa fa-trash" aria-hidden="true"></i>
                                                        </button>
                                                    </td>
                                                </tr>
                                                <tr className="slide-row">
                                                    <td colSpan="4">
                                                        <div id={div_id_2} className="collapse in">
                                                            <div className="slide">
                                                                <table className="table">
                                                                    <thead>
                                                                        <tr>
                                                                            <th>Course ID</th>
                                                                            <th>Course Name</th>
                                                                            <th>Mandatory</th>
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody>
                                                                        { catalog.clearesult_courses.map(({id, course_id, course_name}) => {
                                                                            let btn_text = "Make Mandatory"
                                                                            let action = "add"
                                                                            let mandatory_class_name = "btn-outline-primary"
                                                                            let is_mandatory = false
                                                                            if (mandatory_courses.includes(id)) {
                                                                                mandatory_class_name = "btn-primary"
                                                                                btn_text = "Remove Mandatory"
                                                                                action = "remove"
                                                                                is_mandatory = true
                                                                            }
                                                                            let btn_value = group_id+","+mandatory_table_id+","+id+","+action

                                                                            return (
                                                                                <tr key={id}>
                                                                                    <td>{course_id}</td>
                                                                                    <td>{course_name}</td>
                                                                                    <td>
                                                                                        <ToggleSwitch
                                                                                            value={btn_value = group_id+","+mandatory_table_id+","+id+","+action}
                                                                                            updateMandatoryCourses={updateMandatoryCourses}
                                                                                            is_mandatory={is_mandatory}
                                                                                        />

                                                                                    </td>
                                                                                </tr>
                                                                            );
                                                                        })}

                                                                    </tbody>
                                                                </table>
                                                            </div>
                                                        </div>
                                                    </td>
                                                </tr>
                                            </React.Fragment>
                                        );
                                    })}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </td>
                </tr>
            </React.Fragment>
            )}
        )
    }

    return (
        <div className="table-responsive">
            <table id='groups' className="table">
                <thead>
                    <tr>{renderHeader()}</tr>
                </thead>
                <tbody>
                    {renderBody()}
                </tbody>
            </table>
        </div>
    );
}

export default Table
