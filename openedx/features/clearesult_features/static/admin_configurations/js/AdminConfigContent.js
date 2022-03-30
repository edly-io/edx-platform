import React, {useState, useEffect} from 'react';
import { ToastsContainer, ToastsStore } from "react-toasts";
import Cookies from "js-cookie";
import MandatoryCoursesConfig from './components/MandatoryCoursesConfig';
import MandatoryCoursesConfigModal from './components/MandatoryCoursesConfigModal';
import MandatoryCoursesConfigTable from './components/MandatoryCoursesConfigTable';

import HttpClient from "../../continuing_education/js/client";


export default function AdminConfigContent({context}) {
    const [availableSites, setAvailableSites] = useState([])
    const [siteMandatoryCourses, setSiteMandatoryCourses] = useState([])

    const [selectedSite, setSelectedSite] = useState("")
    const [selectedCourse, setSelectedCourse] = useState("")

    const [allottedTime, setAllottedTime] = useState("")
    const [notificationTime, setNotificationTime] = useState("")

    const [notificationTimeNormalCourses, setNotificationTimeNormalCourses] = useState("")
    const [notificationTimeEventCourses, setNotificationTimeEventCourses] = useState("")


    const [isSiteConfig, setIsSiteConfig] = useState(true)

    const client = new HttpClient({
        headers: {
            "X-CSRFToken": Cookies.get("csrftoken"),
        },
    });

    const loadInitialData = async () => {
        try {
            const availableSitesData = (await client.get(`${context.MANDATORY_COURSES_SITE_LEVEL_CONFIG_URL}`)).data
            setAvailableSites(availableSitesData)
            if (availableSitesData[0]){
                fetchMandatoryCoursesConfigs(availableSitesData[0])
            }
        } catch(e) {
            console.error(e);
        }
    }

    const fetchMandatoryCoursesConfigs = async (site) => {
        try {
                setAllottedTime(site.mandatory_courses_allotted_time)
                setNotificationTime(site.mandatory_courses_notification_period)
                setNotificationTimeNormalCourses(site.courses_notification_period)
                setNotificationTimeEventCourses(site.events_notification_period)
                setSelectedSite(site)

                const mandatory_courses_data = (await client.get(`${context.SITE_MANDATORY_COURSES_LIST_URL}${site.id}/`)).data
                setSiteMandatoryCourses(mandatory_courses_data)

        } catch(e) {
            console.error(e);
        }
    }

    useEffect(() => {
        loadInitialData();
    }, [])

    const updateMandatoryCoursesSiteLevelConfig = async () => {
        const updatedSiteConfig = (await client.post(`${context.MANDATORY_COURSES_SITE_LEVEL_CONFIG_URL}${selectedSite.id}/`, {
            mandatory_courses_allotted_time: allottedTime,
            notification_period_mandatory_courses: notificationTime,
            notification_period_event_courses: notificationTimeEventCourses,
            notification_period_normal_courses: notificationTimeNormalCourses
        })).data
        setAvailableSites(
            availableSites.map((siteConfig) => updatedSiteConfig.id == siteConfig.id ? updatedSiteConfig : siteConfig)
        );
        setSelectedSite(updatedSiteConfig)
    }

    const updateMandatoryCoursesCourseLevelConfig = async () => {
        let updatedCourseConfig = null
        if (selectedCourse.course_config)
        {
            updatedCourseConfig = (await client.patch(`${context.MANDATORY_COURSES_COURSE_LEVEL_CONFIG_URL}${selectedSite.id}/${selectedCourse.course_config.id}/`, {
                mandatory_courses_allotted_time: allottedTime,
                mandatory_courses_notification_period: notificationTime
            })).data
        }
        else {
            updatedCourseConfig = (await client.post(`${context.MANDATORY_COURSES_COURSE_LEVEL_CONFIG_URL}${selectedSite.id}/`, {
                mandatory_courses_allotted_time: allottedTime,
                mandatory_courses_notification_period: notificationTime,
                course_id: selectedCourse.course_id,
                site: selectedSite.id
            })).data
        }
        let updatedMandatoryCourse = selectedCourse
        updatedMandatoryCourse.course_config = {
            id: updatedCourseConfig.id,
            mandatory_courses_allotted_time: updatedCourseConfig.mandatory_courses_allotted_time,
            mandatory_courses_notification_period: updatedCourseConfig.mandatory_courses_notification_period,
        }
        setSiteMandatoryCourses(
            siteMandatoryCourses.map((mandatoryCourse) => mandatoryCourse.id == updatedMandatoryCourse.id ? updatedMandatoryCourse : mandatoryCourse)
        );
        setSelectedCourse(updatedMandatoryCourse)
    }

    const updateMandatoryCoursesConfig = (event) => {
        event.preventDefault()
        try {
            if (isSiteConfig && selectedSite)
            {
                updateMandatoryCoursesSiteLevelConfig()
                ToastsStore.success(`Configurations has been updated for ${selectedSite.domain}.`);
            }
            if (!isSiteConfig && selectedCourse)
            {
                updateMandatoryCoursesCourseLevelConfig()
                ToastsStore.success(`Configurations has been updated for ${selectedCourse.course_name}.`);
            }
        } catch(e) {
            console.error(e);
            ToastsStore.error(`Unable to update Configurations of ${selectedCourse.course_name}.`);
        }
    }

    const deleteCourseConfig = async (mandatoryCourse) => {
        try {
            if (mandatoryCourse.course_config)
            {
                await client.delete(`${context.MANDATORY_COURSES_COURSE_LEVEL_CONFIG_URL}${selectedSite.id}/${(mandatoryCourse.course_config.id)}/`)
                mandatoryCourse.course_config = null
                setSiteMandatoryCourses(
                    siteMandatoryCourses.map((course) => course.id == mandatoryCourse.id ? mandatoryCourse : course)
                );
                setSelectedCourse(mandatoryCourse)
            }
            ToastsStore.success(`Configurations has been updated for ${mandatoryCourse.course_name}.`);
        } catch(e) {
            console.error(e);
            ToastsStore.error(`Unable to reset Configurations of ${mandatoryCourse.course_name}.`);
        }
    }

    const changedSiteHandler = (site_id) => {
        let site = availableSites.find((site) => site.id==site_id)
        fetchMandatoryCoursesConfigs(site)
    }

    const resetBtnClickHandler = (course_id) => {
        let mandatoryCourse = siteMandatoryCourses.find((course) => course.course_id==course_id)
        deleteCourseConfig(mandatoryCourse)
    }

    const editBtnClickHandler = (isSite, course_id=null) => {
        if (!isSite && course_id) {
            let mandatoryCourse = siteMandatoryCourses.find((course) => course.course_id==course_id)
            setSelectedCourse(mandatoryCourse)
            if(mandatoryCourse.course_config)
            {
                setAllottedTime(mandatoryCourse.course_config.mandatory_courses_allotted_time)
                setNotificationTime(mandatoryCourse.course_config.mandatory_courses_notification_period)
            } else{
                setAllottedTime(selectedSite.mandatory_courses_allotted_time)
                setNotificationTime(selectedSite.mandatory_courses_notification_period)
            }
        } else {
            setAllottedTime(selectedSite.mandatory_courses_allotted_time)
            setNotificationTime(selectedSite.mandatory_courses_notification_period)
        }
        setIsSiteConfig(isSite)
    }

    return (
        <div>
            <MandatoryCoursesConfig
                availableSites={availableSites}
                changedSiteHandler={changedSiteHandler}
                dueDateConfigbtnText={(isSiteConfig && allottedTime == "" && notificationTime == "") ? "Add Config": "Update Config"}
                editBtnClickHandler={editBtnClickHandler}
            />
            <MandatoryCoursesConfigModal
                allottedTime={allottedTime}
                setAllottedTime={setAllottedTime}
                notificationTime={notificationTime}
                notificationTimeNormalCourses={notificationTimeNormalCourses}
                setNotificationTimeNormalCourses={setNotificationTimeNormalCourses}
                notificationTimeEventCourses={notificationTimeEventCourses}
                setNotificationTimeEventCourses={setNotificationTimeEventCourses}
                setNotificationTime={setNotificationTime}
                title={isSiteConfig ? selectedSite.domain: selectedCourse.course_name}
                updateMandatoryCoursesConfig={updateMandatoryCoursesConfig}
                isSiteConfig={isSiteConfig}
            />
            <MandatoryCoursesConfigTable
                selectedSite={selectedSite}
                siteMandatoryCourses={siteMandatoryCourses}
                editBtnClickHandler={editBtnClickHandler}
                resetBtnClickHandler={resetBtnClickHandler}
            />
            <ToastsContainer store={ToastsStore} />
        </div>
    )
}
