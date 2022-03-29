import React, { useState, useEffect } from 'react';

const Table = ({groups, handleEditClick, DeleteGroup}) => {
    const renderHeader = () => {
        let headerElement = ['#', 'id', 'name', 'site', 'actions']

        return headerElement.map((key, index) => {
            return <th className={key} key={index}>{key.toUpperCase()}</th>
        })
    }

    const renderBody = () => {
        if (!groups || !groups.length){
            return <tr></tr>
        }

        return groups.map(({ id, name, site, users}) => {
            const div_id = "#collapse" + id
            const div_id2 = "collapse" + id

            return (
            <React.Fragment key={id}>
                <tr>
                    <td>
                        <a className="row-opener collapsed" data-toggle="collapse" href={div_id} role="button" aria-expanded="false" aria-controls={div_id2}>
                            <i className="fa fa-chevron-right" aria-hidden="true"></i>
                        </a>
                    </td>
                    <td><span>{id}</span> {(id != site.default_group) ? '': <span className="badge badge-info">Default</span>}</td>
                    <td>{name}</td>
                    <td>{site.domain}</td>
                    <td className="actions">
                        <button type="button" data-toggle="modal" data-target="#exampleModalCenter" value={id} onClick={(e)=>handleEditClick(id)} >
                            <i className="fa fa-pencil" aria-hidden="true"></i>
                        </button>
                        <button type="button" value={id} disabled={!(id != site.default_group)} onClick={(e)=> {if (window.confirm('Are you sure you wish to delete this item?')) DeleteGroup(id)}} >
                            <i className="fa fa-trash" aria-hidden="true"></i>
                        </button>
                    </td>
                </tr>
                <tr className="slide-row">
                    <td colSpan="5">
                        <div id={div_id2} className="collapse in">
                            <div className="slide">
                                <table className="table">
                                    <thead>
                                        <tr>
                                            <th>Username</th>
                                            <th>Email</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        { users.map(({id, username, email}) => {
                                            return (
                                                <tr key={id}>
                                                    <td>{username}</td>
                                                    <td>{email}</td>
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
