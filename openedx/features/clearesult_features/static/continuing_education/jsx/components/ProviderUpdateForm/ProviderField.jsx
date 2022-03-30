import React, { useState } from "react";

export default function ProviderField({ id, providerName, providerId, handleUpdate, handleDelete }) {
    const [updatedId, setUpdatedId] = useState(providerId);
    const [canUpdate, setCanUpdate] = useState(false);

    function handleClick() {
        handleUpdate(id, updatedId);
        setCanUpdate(false);
    }

    function handleChange(event) {
        const newValue = event.target.value;
        if (newValue.length >= 20) return;
        setUpdatedId(newValue);
        if (event.target.value.trim() === "") {
            setCanUpdate(false);
        } else if (!canUpdate) {
            setCanUpdate(true);
        }
    }

    return (
        <div className="update-block">
            <label>Organization</label>
            <input placeholder="Organization" readOnly={true} disabled value={providerName} />
            <label>Account ID</label>
            <input placeholder="Account ID" value={updatedId} onChange={handleChange}></input>
            <div className="btn-holder">
                <button className="btn btn-primary" disabled={!canUpdate} onClick={handleClick}>
                    Update
                </button>
                <button className="btn btn-primary red" onClick={() => handleDelete(id)}>
                    Delete
                </button>
            </div>
        </div>
    );
}
