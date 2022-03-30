import React from "react";
import ReactDOM from "react-dom";

import ContinuingEducationForm from "./ContinuingEducationForm";

export class ContinuingEducation {
    constructor(context) {
        ReactDOM.render(<ContinuingEducationForm context={context} />, document.getElementById("root"));
    }
}
