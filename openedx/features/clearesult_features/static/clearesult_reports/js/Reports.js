import React from "react";
import ReactDOM from "react-dom";

import ReportsContent from "./ReportsContent";

export class Reports {
    constructor(context) {
        ReactDOM.render(<ReportsContent context={context} />, document.getElementById("root"));
    }
}
