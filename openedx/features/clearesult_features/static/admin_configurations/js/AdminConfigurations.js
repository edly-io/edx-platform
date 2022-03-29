import React from "react";
import ReactDOM from "react-dom";

import AdminConfigContent from "./AdminConfigContent";

export class AdminConfigurations {
    constructor(context) {
        ReactDOM.render(<AdminConfigContent context={context} />, document.getElementById("root"));
    }
}
