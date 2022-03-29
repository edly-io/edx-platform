import React, { useState } from "react";

function ToggleSwitch({
    value,
    updateMandatoryCourses,
    is_mandatory
}) {
    const [isToggled, setIsToggled] = useState(is_mandatory);
    const onToggle = (ids) => {
        setIsToggled(!isToggled);
        updateMandatoryCourses(ids)
    }

    return (
      <label className="toggle-switch">
        <input
            value={value} type="checkbox" checked={isToggled}
            onChange={(e) => {
                if (window.confirm('Are you sure you want to update mandatory course?')) onToggle(e.target.value)}
            }
        />
        <span className="switch" />
      </label>
    );
  }
  export default ToggleSwitch;
