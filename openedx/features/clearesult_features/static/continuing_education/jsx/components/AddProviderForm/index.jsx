import React, { useState, useEffect } from "react";

export default function AddProviderForm({ choices, handleAddProvider, handleOnSkipClick, visitingForSecondTime }) {
    const [selected, setSelected] = useState("");
    const [providerId, setProviderId] = useState("");

    useEffect(() => {
        if (choices.length) {
            setSelected(choices[0].id);
        }
    }, [choices]);

    function handleOnClick() {
        handleAddProvider(selected, providerId);
        setProviderId("");
    }

    function handleProviderIdChange(event) {
        const newValue = event.target.value;
        if (newValue.length >= 20) return;
        setProviderId(newValue);
    }

    function renderButton() {
        if (window.location.search.includes('first_time=true')
            || document.referrer.includes('/register')) {
          return (
            <button
                className="btn btn-primary"
                onClick={handleOnSkipClick}
            >
                Continue
            </button>
          );
        } else {
          return (
              <div></div>
          );
        }
    }

    return (
        <div>
            <h2>Add a new account</h2>
            <p>If you have a continuing education account, please select the name of the organization and enter your account ID, then click Add.
                You can come back to this page at any time to add or edit your continuing education account(s).</p>
            <br></br>
            <div className="form-holder">
                {choices.length ? (
                    <div className="form-area">
                        <label>Organization</label>
                        <select
                            required
                            name="provider"
                            id="provider"
                            value={selected}
                            onChange={(e) => setSelected(e.target.value)}
                        >
                            {choices &&
                                choices.map((provider) => (
                                    <option key={provider.id} value={provider.id}>
                                        {provider.name}
                                    </option>
                                ))}
                        </select>
                        <label>Account ID</label>
                        <input
                            required
                            placeholder="Account ID"
                            value={providerId}
                            onChange={handleProviderIdChange}
                        ></input>
                        <div className="btn-holder">
                            <button
                                disabled={choices.length === 0 || !providerId.trim()}
                                className="btn btn-primary"
                                onClick={handleOnClick}
                            >
                                Add
                            </button>
                            {renderButton()}
                        </div>
                    </div>
                ) : (
                    <div>
                        <p className="text-error">No new organization is available at the moment.</p>
                        <br />
                    </div>
                )}
            </div>
        </div>
    );
}
