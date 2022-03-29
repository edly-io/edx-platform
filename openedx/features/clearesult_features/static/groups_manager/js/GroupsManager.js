import React from "react";
import ReactDOM from "react-dom";

import GroupsContent from "./GroupsContent";

export class GroupsManager {
    constructor(context) {
        ReactDOM.render(<GroupsContent context={context} />, document.getElementById("root"));
    }
}
