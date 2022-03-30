import React, {useState, useEffect} from 'react';
import { ToastsContainer, ToastsStore } from "react-toasts";
import Cookies from "js-cookie";
import Table from '../../group_catalogs_manager/js/components/Table';
import AddLinkageModal from '../../group_catalogs_manager/js/components/AddLinkageModal';
import HttpClient from "../../continuing_education/js/client";

export default function GroupCatalogsContent({context}) {
    const [groupCatalogs, setGroupCatalogs] = useState([])

    const [site, setSite] = useState({})
    const [catalogs, setCatalogs] = useState([])
    const [groups, setGroups] = useState([])

    const [currentSiteCatalogs, setCurrentSiteCatalogs] = useState([])
    const [currentSiteGroups, setCurrentSiteGroups] = useState([])

    const [availableSites, setAvailableSites] = useState([])

    const client = new HttpClient({
        headers: {
            "X-CSRFToken": Cookies.get("csrftoken"),
        },
    });

    const loadInitialData = async () => {
        try {
            const groupCatalogsData = (await client.get(`${context.GROUP_CATALOGS_URL}`)).data
            setGroupCatalogs(groupCatalogsData)
            const availableSitesData = (await client.get(`${context.AVAILABLE_SITES_LIST_URL}`)).data
            setAvailableSites(availableSitesData)

            setSite(availableSitesData[0])
        } catch(e) {
            console.error(e);
        }
    }

    const loadCurrentSiteCatalogsGroups = async () => {
        if (site.id) {
            let catalogs_url = `${context.SITE_CATALOGS_LIST_URL_PREFIX}${site.id}/`;
            let groups_url = `${context.SITE_GROUPS_LIST_URL_PREFIX}${site.id}/`;

            try {
                const siteCatalogs = (await client.get(catalogs_url)).data
                const siteGroups = (await client.get(groups_url)).data

                setCurrentSiteCatalogs(siteCatalogs)
                setCurrentSiteGroups(siteGroups)

            } catch(e) {
                console.error(e);
            }
        }
    }

    useEffect(() => {
        loadCurrentSiteCatalogsGroups();
    }, [site])

    useEffect(() => {
        loadInitialData();
    }, [])

    const updateGroupCatalogs = async (action, groups_list, catalogs_list) => {
        try{
            let updatedGroupCatalogs = (await client.post(`${context.UPDATE_GROUP_CATALOGS_URL}`, {
                action: action,
                groups: groups_list,
                catalogs: catalogs_list
            })).data;

            setGroupCatalogs(
                groupCatalogs.map((groupCatalog) => {
                    const is_updated = updatedGroupCatalogs.find((updatedGroupCatalog) => updatedGroupCatalog.id == groupCatalog.id)
                    return is_updated ? is_updated : groupCatalog
                })
            )

            ToastsStore.success("Catalogs linkage have been updated.");
        } catch (ex) {
            console.error(ex);
            ToastsStore.error("Could not update a catalogs linkage.");
        }
    }

    const updateMandatoryCourses = async (ids) => {
        try{
            const [groupId, mandatory_id, course_id, action] = ids.split(',')
            let updatedCatalog = (await client.patch(`${context.UPDATE_MANDATORY_COURSES_URL_PREFIX}${mandatory_id}/`, {
                action: action,
                mandatory_courses: [parseInt(course_id, 10)],
            })).data;

            let changed_group = groupCatalogs.find((groupCatalog) => groupCatalog.id == groupId)
            let updated_catalogs = changed_group.catalogs.map((catalog)=>catalog.id==updatedCatalog.id ? updatedCatalog: catalog)
            changed_group.catalogs = updated_catalogs

            setGroupCatalogs(
                groupCatalogs.map( groupCatalog => changed_group.id == groupCatalog.id ? changed_group : groupCatalog)
            )

            ToastsStore.success("Course has been updated.");
        } catch (ex) {
            console.error(ex);
            ToastsStore.error("Could not update a Course.");
        }
    }

    const addNewLinkage = () => {
        // let groups_list = groups
        // let catalogs_list = catalogs
        updateGroupCatalogs("add", groups, catalogs)
        setGroups([])
        setCatalogs([])
    }

    const removeCatalog = async (ids) => {
        const [groupId, catalogId] = ids.split(',')
        updateGroupCatalogs("remove", [parseInt(groupId, 10)], [parseInt(catalogId, 10)])
    }

    return (
        <div>
            <div className="admin-header">
            <h2>Groups Catalogs Linkage Manager</h2>
                <div className="form-inline">
                    <button type="button" className="btn btn-primary" data-toggle="modal" data-target="#addLinkageModal" >
                        Add
                    </button>
                </div>
            </div>
            <AddLinkageModal
                availableSites={availableSites}
                setSite={setSite}
                currentSiteCatalogs={currentSiteCatalogs}
                currentSiteGroups={currentSiteGroups}
                catalogs={catalogs}
                setCatalogs={setCatalogs}
                groups={groups}
                setGroups={setGroups}
                addNewLinkage={addNewLinkage}
            />
            <Table
                groupCatalogs={groupCatalogs}
                removeCatalog={removeCatalog}
                updateMandatoryCourses={updateMandatoryCourses}
            />
            <ToastsContainer store={ToastsStore} />
        </div>
    )
}
