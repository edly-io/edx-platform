/* globals gettext */

import PropTypes from "prop-types";
import React from "react";

import { InputText } from "@edx/paragon/static";

class PasswordResetInput extends React.Component {
    constructor(props) {
        super(props);
        this.state = {
            inputType: "password",
        };
        this.setInputType = this.setInputType.bind(this);
    }

    setInputType() {
        if (this.state.inputType === "password") {
            this.setState({
                inputType: "text",
            });
        } else {
            this.setState({ inputType: "password" });
        }
    }

    render() {
        return (
            <div className="form-field" style={{ position: "relative" }}>
                <InputText
                    id={this.props.name}
                    type={this.state.inputType}
                    themes={["danger"]}
                    dangerIconDescription={gettext("Error: ")}
                    required
                    {...this.props}
                />
                <span
                    style={{
                        position: "absolute",
                        right: "10px",
                        top: `${this.props.isValid ? "45%" : "35%"}`,
                    }}
                    onClick={this.setInputType}
                    className={`fa fa-fw field-icon ${this.state.inputType === "password" ? "fa-eye" : "fa-eye-slash"}`}
                ></span>
            </div>
        );
    }
}

PasswordResetInput.propTypes = {
    name: PropTypes.string.isRequired,
};

export default PasswordResetInput;
