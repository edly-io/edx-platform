import React from "react";
import ReactDOM from "react-dom";

import GroupCatalogsContent from "./GroupCatalogsContent";

export class GroupCatalogsManager {
    constructor(context) {
        ReactDOM.render(<GroupCatalogsContent context={context} />, document.getElementById("root"));
    }
}
