import React from "react";
import ReactDOM from "react-dom";

import ParticipationCodeContent from "./ParticipationCodeContent";

export class ParticipationCode {
    constructor(context) {
        ReactDOM.render(<ParticipationCodeContent context={context} />, document.getElementById("root"));
    }
}
