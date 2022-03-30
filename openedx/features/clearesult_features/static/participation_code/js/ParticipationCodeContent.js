import React, { useEffect, useState } from "react";
import Cookies from "js-cookie";
import HttpClient from "../../continuing_education/js/client";

export default function ParticipationCodeContent({ context }) {
  const [participationCode, setParticipationCode] = useState("");
  const [codeBtnState, setCodeBtnState] = useState(false);
  const [participatedGroups, setParticipatedGroups] = useState([]);

  const client = new HttpClient({
    headers: {
      "X-CSRFToken": Cookies.get("csrftoken"),
    },
  });

  const handleParticipationCodeField = (value) => {
    value = value.trim();
    setParticipationCode(value);
    if (value == "") {
      setCodeBtnState(false);
    } else {
      setCodeBtnState(true);
    }
  };

  const handleCodeBtnClick = async () => {
    setCodeBtnState(false);
    try {
      const data = (
        await client.post(`${context.ADD_USER_TO_PARTICIPATED_GROUP_URL}`, {
          code: participationCode,
        })
      ).data;
      alert(data.message);
      setParticipationCode("");
      loadInitialData();
    } catch (e) {
      alert("Oops! Looks like you entered an invalid code. Please try again.");
    }
  };

  const handleContinueBtn = () => {
    window.location.href = context.CONTINUING_EDUCATION_URL;
  };

  const loadInitialData = async () => {
    try {
      const groupsData = (await client.get(`${context.PARTICIPATED_GROUPS_LIST_URL}`)).data;
      setParticipatedGroups(groupsData);
    } catch (e) {
      console.log(e.message);
    }
  };

  useEffect(() => {
    loadInitialData();
  }, []);

  const renderContinueBtn = () => {
    if (window.location.search.includes('first_time=true')
        || document.referrer.includes('/register')) {
          return (
            <a className="btn btn-primary" onClick={handleContinueBtn}>
                Continue
            </a>
          );
      }
  }

  const renderParticipatedGroups = () => {
    if (participatedGroups.length > 0) {
      return (
        <div>
          <p>You are participating in the following program(s):</p>
          <ul>
            {participatedGroups.map((group) => (
              <li key={group.name}>{group.name}</li>
            ))}
          </ul>
          <p>
            Please <a href={context.CONTACT_US_URL}>contact us</a> for any question or concern regarding your
            program participation.
          </p>
        </div>
      );
    }
  };

  return (
    <div className="clearesult-reports">
      <div>
        <p>
          Please enter the participation code that was provided to you. To get a participation code,{" "}
          <a href={context.CONTACT_US_URL}>contact our team</a> and we'll be in touch shortly.
        </p>
        <input
          type="text"
          value={participationCode}
          className="form-control"
          onChange={(e) => handleParticipationCodeField(e.target.value)}
        />
        <div className="participation-btns">
            <a className={`btn btn-primary ${codeBtnState ? "" : "disabled"}`} onClick={handleCodeBtnClick}>
            Add
            </a>
            {renderContinueBtn()}
        </div>
      </div>
      <div>{renderParticipatedGroups()}</div>
    </div>
  );
}
