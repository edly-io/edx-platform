import React, { useState, useEffect } from 'react';


const Table = ({earnedCredits}) => {
    const renderHeader = () => {
        let headerElement = ['Organization'].concat(earnedCredits.years)
        return headerElement.map((key, index) => <th colSpan="1" key={index}>{key}</th>)
    }

    const renderBody = () => {
        return earnedCredits.providers.map((provider, index) => {
            return (
            <React.Fragment key={index}>
                <tr>
                    <td>{provider}</td>
                    {
                        earnedCredits.years.map(year => {
                            return (
                                <React.Fragment key={year}>
                                    <td>{earnedCredits.credit_report[provider][year]}</td>
                                </React.Fragment>
                            )
                        })
                    }
                </tr>
            </React.Fragment>
            )}
        )
    }

    if (!Object.keys(earnedCredits).length || earnedCredits.years.length == 0) {
        return (
            <div>
                <h2>
                    Summary of earned credits
                </h2>
                <p>No credits earned yet</p>
            </div>
        )
    }

    return (
        <div className="table table-striped table-responsive">
            <h2>
                Summary of earned credits
            </h2>
            <table id='continuing-education-ids' className="table">
                <thead>
                    <tr>{renderHeader()}</tr>
                </thead>
                <tbody>
                    {renderBody()}
                </tbody>
            </table>
        </div>
    );
}

export default Table
