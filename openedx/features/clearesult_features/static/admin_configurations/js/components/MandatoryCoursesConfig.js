import React, {useState, useEffect} from 'react';
import Cookies from "js-cookie";


export default function MandatoryCoursesConfig({availableSites, changedSiteHandler, dueDateConfigbtnText, editBtnClickHandler}) {
    const siteOptions = availableSites.map(
        (availableSite) => {
            return <option key={availableSite.id} value={availableSite.id} >{availableSite.domain}</option>
        }
    )
    return (
        <div className='admin-header'>
            <div className='form-inline'>
                <span> Site:
                    <select className='form-control' onChange={(e) => changedSiteHandler(e.target.value)}>
                        {siteOptions}
                    </select>
                </span>
                <button
                        type="button"
                        className="btn btn-primary"
                        data-toggle="modal"
                        data-target="#exampleModalCenter"
                        onClick={(event)=>{editBtnClickHandler(true)}}
                    >
                        {dueDateConfigbtnText}
                </button>
            </div>
        </div>
    )
}
