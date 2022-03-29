import React from 'react';
const Table = ({
    isSuperUser,
    availableCatalogs,
    handleDeleteCatalogButton,
    handleEditCatalogButton
}) => {
    const renderHeader = () => {
        let headerElement = ['#', 'id', 'name', 'site', 'actions']
        return headerElement.map((key, index) => {
            return <th className={key} key={index}>{key.toUpperCase()}</th>
        })
    }

    const renderBody = () => {
        if (availableCatalogs === null || availableCatalogs === [] || availableCatalogs === undefined){
            return <h1>No data available. </h1>
        }
        return availableCatalogs.map(({ id, name, site, clearesult_courses}) => {
            const div_id = "#collapse" + id
            const div_id2 = "collapse" + id
            return (
            <React.Fragment key={id}>
                <tr>
                    <td>
                        <a className='row-opener collapsed' data-toggle="collapse" href={div_id} role="button" aria-expanded="false" aria-controls={div_id2}>
                            <i className="fa fa-chevron-right" aria-hidden="true"></i>
                        </a>
                    </td>
                    <td>{id}</td>
                    <td>{name}</td>
                    <td>{(site === null) ? "public" : site.domain}</td>
                    <td className="actions">
                        <button type="button" className={(site === null && isSuperUser == 0) ? "disabled" : ""} data-toggle="modal" data-target="#exampleModalCenter" value={id} onClick={(event)=>handleEditCatalogButton(id)} >
                            <i className="fa fa-pencil" aria-hidden="true"></i>
                        </button>
                        <button type="button" className={(site === null && isSuperUser == 0) ? "disabled" : ""} value={id} onClick={(event)=>handleDeleteCatalogButton(id)} >
                            <i className="fa fa-trash" aria-hidden="true"></i>
                        </button>
                    </td>
                </tr>
                <tr className="slide-row">
                    <td colSpan="5">
                        <div id={div_id2} className="collapse in">
                            <div className="slide">
                                <table className="table table-striped">
                                    <thead>
                                        <tr>
                                            <th>ID</th>
                                            <th>Course Name</th>
                                            <th>Course ID</th>
                                            <th>Site</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        { clearesult_courses.map(({id, course_name, site, course_id}) => {
                                            return (
                                                <tr key={id}>
                                                    <td>{id}</td>
                                                    <td>{course_name}</td>
                                                    <td>{course_id}</td>
                                                    <td>{(site === null) ? "public" : site.domain}</td>
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
            )}
        )
    }

    return (
        <div className="table-responsive">
            <table id='catalogs' className="table">
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
