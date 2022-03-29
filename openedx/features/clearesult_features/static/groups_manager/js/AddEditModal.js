import React, { useEffect, useState } from "react";
import DualListBox from 'react-dual-listbox';


export default function AddEditModal({
        availableSites,
        currentSiteUsers,
        name,
        setName,
        site,
        setSite,
        users,
        setUsers,
        isEdit,
        handleAddOrEditClick,
        isDefault
    }) {

    const submitButtonText = isEdit ? "Update" : "Add"

    const siteOptions = availableSites.map(
        (availableSite) => {
            if  (availableSite.id == site.id)
            {
                return <option key={availableSite.id} value={availableSite.id} selected>{availableSite.domain}</option>
            } else {
                return <option key={availableSite.id} value={availableSite.id}>{availableSite.domain}</option>
            }
        }
    )

    const userOptions = currentSiteUsers.map((currentSiteUser) => {
        return {'value': currentSiteUser.id, 'label': currentSiteUser.email}
    });

    const submitHandler = (e) => {
        e.preventDefault();
        handleAddOrEditClick();
    }

    const siteChangeHandler = (e) => {
        setSite(availableSites.filter((avialbleSite) => avialbleSite.id == e.target.value)[0]);
    }

    const validator = (val) => {
        this.error = [];
        this.val = val;
        this.isRequired = function(){
          if (!this.val) {
            this.error.push('This field is required');
          }
          return this;
        }
        this.isEmail = function() {
         const filter = /^([a-zA-Z0-9_\.\-])+\@(([a-zA-Z0-9\-])+\.)+([a-zA-Z0-9]{2,4})+$/;
         if (this.val && !filter.test(this.val)) {
            this.error.push('Invalid Email');
         }
         return this;
        }
        return this;
    }

    return (
        <div>
            <div className="modal fade modal-update" id="exampleModalCenter" tabIndex="-1" role="dialog" aria-labelledby="exampleModalCenterTitle" aria-hidden="true">
                <div className="modal-dialog modal-dialog-centered modal-lg" role="document">
                    <div className="modal-content">
                        <form onSubmit={(e)=>submitHandler(e)}>
                            <div className="modal-header">
                                <h5 className="modal-title" id="exampleModalLongTitle">Add Group</h5>
                                <button type="button" className="close" data-dismiss="modal" aria-label="Close">
                                <span aria-hidden="true">&times;</span>
                                </button>
                            </div>
                            <div className="modal-body">
                                <div className="form-group">
                                    <label htmlFor="clearesultGroupName">Group Name</label>
                                    <input  validate={(val) => new validator(val).isRequired().error } type="text" className="form-control" id="clearesultGroupName" aria-describedby="clearesultGroupNameHelp" placeholder="Enter Group Name" required value={name} onChange={(e) => setName(e.target.value)} />
                                    <small id="clearesultGroupNameHelp" className="form-text text-muted">We'll never share your email with anyone else.</small>
                                </div>
                                <div className="form-group">
                                    <label htmlFor="clearesultGroupSite">Site</label>
                                    <select className="form-control" id="clearesultGroupSite" disabled={isEdit} onChange={(e) => siteChangeHandler(e)}>
                                        {siteOptions}
                                    </select>
                                </div>
                                <div className="form-group">
                                    <label htmlFor="clearesultGroupUsers">Users</label>
                                    <div className="form-control" id="clearesultGroupUsers" >
                                        <DualListBox
                                            canFilter
                                            options={userOptions}
                                            selected={users}
                                            onChange={(selected) => setUsers(selected)}
                                            disabled={isDefault}
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
