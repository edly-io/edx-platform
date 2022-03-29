import React, { useEffect, useState } from "react";
import DualListBox from 'react-dual-listbox';
export default function AddEditModal({
        catalogName,
        availableSites,
        availableFilteredCourses,
        selectedCourses,
        setSelectedCourses,
        handleSubmit,
        isEditButton,
        setCatalogName,
        handleSiteSelect,
        selectedSite,
    }) {

    const submitButtonText = isEditButton ? "Update" : "Add";
    let siteOptions = null;
    if (isEditButton) {
        siteOptions = <option key={selectedSite.id} value={selectedSite.id}>{selectedSite.domain}</option>
    } else {
        siteOptions = availableSites.map(
            (availableSite) => <option key={availableSite.id} value={availableSite.id}>{availableSite.domain}</option>
        )
    }

    const courseOptions = availableFilteredCourses.map((availableFilteredCourse) => {
        return {'value': availableFilteredCourse.course_id, 'label': availableFilteredCourse.course_name}
    });
    return (
        <div>
            <div className="modal fade modal-update" id="exampleModalCenter" tabIndex="-1" role="dialog" aria-labelledby="exampleModalCenterTitle" aria-hidden="true">
                <div className="modal-dialog modal-lg modal-dialog-centered" role="document">
                    <div className="modal-content">
                        <form onSubmit={(event)=>handleSubmit(event)}>
                            <div className="modal-header">
                                <h5 className="modal-title" id="exampleModalLongTitle">{isEditButton ? "Update" : "Add"} Catalog</h5>
                                <button type="button" className="close" data-dismiss="modal" aria-label="Close">
                                <span aria-hidden="true">&times;</span>
                                </button>
                            </div>
                            <div className="modal-body">
                                <div className="form-group">
                                    <label htmlFor="clearesultCatalogName">Catalog Name</label>
                                    <input type="text" className="form-control" required id="clearesultCatalogName" aria-describedby="clearesultCatalogNameHelp" placeholder="Enter Catalog Name" value={catalogName} onChange={(event) => setCatalogName(event.target.value)} />
                                </div>
                                <div className="form-group">
                                    <label htmlFor="clearesultCatalogSite">Site</label>
                                    <select className="form-control" id="clearesultCatalogSite" value={selectedSite.id} disabled={isEditButton} onChange={(event) => handleSiteSelect(event)}>
                                        {siteOptions}
                                    </select>
                                </div>
                                <div className="form-group">
                                    <label htmlFor="clearesultCatalogCourses">Courses</label>
                                    <div className="form-control" id="clearesultCatalogCourses" >
                                        <DualListBox
                                            canFilter
                                            options={courseOptions}
                                            selected={selectedCourses}
                                            onChange={(selected) => setSelectedCourses(selected)}
                                        />
                                    </div>
                                </div>
                            </div>
                            <div className="modal-footer">
                                <button type="button" className="btn btn-secondary" data-dismiss="modal">Close</button>
                                <button type="submit" className="btn btn-primary">{submitButtonText}</button>
                            </div>
                    	</form>
                	</div>
                </div>
            </div>
        </div>
    );
}
