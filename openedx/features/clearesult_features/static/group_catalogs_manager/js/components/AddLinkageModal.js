import React, { useEffect, useState } from "react";
import DualListBox from 'react-dual-listbox';

export default function AddLinkageModal({
        availableSites,
        setSite,
        currentSiteCatalogs,
        currentSiteGroups,
        catalogs,
        setCatalogs,
        groups,
        setGroups,
        addNewLinkage
    }) {

    const siteOptions = availableSites.map(
        (availableSite) => {
            return <option key={availableSite.id} value={availableSite.id} >{availableSite.domain}</option>
        }
    )

    const groupOptions = currentSiteGroups.map((currentSiteGroup) => {
        return {'value': currentSiteGroup.id, 'label': currentSiteGroup.name}
    });

    const catalogOptions = currentSiteCatalogs.map((currentSiteCatalog) => {
        return {'value': currentSiteCatalog.id, 'label': currentSiteCatalog.name}
    });

    const siteChangeHandler = (e) => {
        setSite(availableSites.find((avialbleSite) => avialbleSite.id == e.target.value));
    }

    const submitHandler = (e) => {
        e.preventDefault();
        addNewLinkage()
    }

    return (
        <div>
            <div className="modal fade modal-update" id="addLinkageModal" tabIndex="-1" role="dialog" aria-labelledby="addLinkageModalTitle" aria-hidden="true">
                <div className="modal-dialog modal-dialog-centered modal-lg" role="document">
                    <div className="modal-content">
                        <form onSubmit={(e)=>submitHandler(e)}>
                            <div className="modal-header">
                                <h5 className="modal-title" id="addLinkageModalLongTitle">Link Groups with Catalogs</h5>
                                <button type="button" className="close" data-dismiss="modal" aria-label="Close">
                                <span aria-hidden="true">&times;</span>
                                </button>
                            </div>
                            <div className="modal-body">
                                <div className="form-group">
                                    <label htmlFor="clearesultGroupSite">Site</label>
                                    <select className="form-control" id="clearesultGroupSite" onChange={(e) => siteChangeHandler(e)}>
                                        {siteOptions}
                                    </select>
                                </div>
                                <div className="form-group">
                                    <label htmlFor="addLinkageGroups">Groups</label>
                                    <div className="form-control" id="addLinkageGroups" >
                                        <DualListBox
                                            canFilter
                                            options={groupOptions}
                                            selected={groups}
                                            onChange={(selected) => setGroups(selected)}
                                        />
                                    </div>
                                </div>
                                <div className="form-group">
                                    <label htmlFor="addLinkageCatalogs">Catalogs</label>
                                    <div className="form-control" id="addLinkageCatalogs" >
                                        <DualListBox
                                            canFilter
                                            options={catalogOptions}
                                            selected={catalogs}
                                            onChange={(selected) => setCatalogs(selected)}
                                        />
                                    </div>
                                </div>
                            </div>
                            <div className="modal-footer">
                                <button type="button" className="btn btn-secondary" data-dismiss="modal">Close</button>
                                <button type="submit" className="btn btn-primary">Add</button>
                            </div>
                        </form>
                	</div>
                </div>
            </div>
        </div>
    );
}
