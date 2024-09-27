import React from 'react';
import ReactDOM from 'react-dom';
import UploadContext from "./UploadContext";

export default class GoogleCDNUpload {
  constructor() {
    ReactDOM.render(<UploadContext context={context} />, document.getElementById("root"));
  }
}

export { GoogleCDNUpload }
