import React from "react";
import ProviderField from "./ProviderField";

export default function ProviderUpdateForm({ profiles, handleUpdateProvider, handleDeleteProvider }) {
    return (
        <div>
            <h2>Your account(s)</h2>
            <div className="education-provider-ids">
                    {profiles.length ? (
                        profiles.map((profile) => (
                            <div key={profile.id} className="update-block-holder">
                                <ProviderField
                                    id={profile.id}
                                    providerId={profile.credit_id}
                                    providerName={profile.credit_type_details.name}
                                    handleDelete={handleDeleteProvider}
                                    handleUpdate={handleUpdateProvider}
                                />
                            </div>
                        ))
                    ) : (
                        <p className="text-error error">No account found</p>
                    )}
            </div>
        </div>
    );
}
