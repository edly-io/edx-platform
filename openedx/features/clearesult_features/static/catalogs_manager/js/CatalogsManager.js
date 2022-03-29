import React from "react";
import ReactDOM from "react-dom";
import CatalogsContent from "./CatalogsContent";
export class CatalogsManager {
    constructor(context) {
        ReactDOM.render(<CatalogsContent context={context} />, document.getElementById("root"));
    }
}
