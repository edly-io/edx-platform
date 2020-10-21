import React, { useState } from "react";

export default function ProviderField({ id, providerName, providerId, handleUpdate, handleDelete }) {
    const [updatedId, setUpdatedId] = useState(providerId);
    const [canUpdate, setCanUpdate] = useState(false);

    function handleClick() {
        handleUpdate(id, updatedId);
        setCanUpdate(true);
    }

    function handleChange(event) {
        setUpdatedId(event.target.value);
        if (event.target.value.trim() === "") {
            setCanUpdate(false);
        } else if (!canUpdate) {
            setCanUpdate(true);
        }
    }

    return (
        <div className="update-block">
            <label>Provider</label>
            <input placeholder="Provider" readOnly={true} disabled value={providerName} />
            <label>ID</label>
            <input placeholder="ID" value={updatedId} onChange={handleChange}></input>
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
