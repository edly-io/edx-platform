import React, { useEffect, useState } from "react";
import { ToastsContainer, ToastsStore } from "react-toasts";
import Table from "./Table";
import AddEditModal from "./AddEditModal";
import HttpClient from "../../continuing_education/js/client";
import Cookies from "js-cookie";


export default function GroupsContent({ context }) {
    const [groups, setGroups] = useState([])

    const [isDefault, setIsDefault] = useState(false)
    const [isEdit, setIsEdit] = useState(false)
    const [currentGroup, setCurrentGroup] = useState({})
    const [name, setName] = useState("")
    const [site, setSite] = useState({})
    const [users, setUsers] = useState([])
    const [currentSiteUsers, setCurrentSiteUsers] = useState([])

    const [availableSites, setAvailableSites] = useState([])


    const client = new HttpClient({
        headers: {
            "X-CSRFToken": Cookies.get("csrftoken"),
        },
    });

    const loadInitialData = async () => {
        try {
            const groupsData = (await client.get(`${context.USER_GROUPS_URL}`)).data
            setGroups(groupsData)
            const availableSitesData = (await client.get(`${context.AVAILABLE_SITES_LIST_URL}`)).data
            setAvailableSites(availableSitesData)
        } catch(e) {
            console.error(e);
        }
    }

    const loadCurrentSiteUsers = async () => {
        if (site.id) {
            let url = `${context.SITE_USERS_LIST_URL_PREFIX}${site.id}/`;
            try {
                const siteUsers = (await client.get(url)).data
                setCurrentSiteUsers(siteUsers)
            } catch(e) {
                console.error(e);
            }
        }
    }

    useEffect(() => {
        loadCurrentSiteUsers();
    }, [site])

    useEffect(() => {
        loadInitialData();
    }, [])

    const updateGroup = async () => {
        try {
            const updatedGroup = (await client.patch(`${context.USER_GROUPS_URL}${currentGroup.id}/`, {
                name: name,
                site: site.id,
                users: users
            })).data;

            setGroups(
                groups.map((group) => updatedGroup.id == group.id ? updatedGroup : group)
            );

            ToastsStore.success("Group has been updated.");
        } catch (ex) {
            console.error(ex);
            ToastsStore.error("Could not update a Group.");
        }
    }

    const AddGroup = async () => {
        try {
            const newGroup = (await client.post(`${context.USER_GROUPS_URL}`, {
                name: name,
                site: site.id,
                users: users
            })).data;

            setGroups([newGroup, ...groups]);
            setCurrentGroup(newGroup);
            setIsEdit(true)
            ToastsStore.success("Group has been Added.");
        } catch (ex) {
            console.error(ex);
            ToastsStore.error("Could not add a Group.");
        }
    }

    const DeleteGroup = async (group_id) => {
        try {
            await client.delete(`${context.USER_GROUPS_URL}${group_id}/`);

            setGroups(groups.filter((group) => group.id != group_id));
            setCurrentGroup({});
            ToastsStore.success("Group has been Deleted.");
        } catch (ex) {
            console.error(ex);
            ToastsStore.error("Could not delete a Group.");
        }
    }

    const handleEditClick = (groupId) => {
        const groupItem = groups.filter((group) => group.id == groupId);
        setIsEdit(true);
        setName(groupItem[0].name);
        setSite(groupItem[0].site);
        setUsers(groupItem[0].users.map((user) => user.id));
        setCurrentGroup(groupItem[0]);
        setIsDefault(groupItem[0].id == groupItem[0].site.default_group)
    }

    const handleAddClick = () => {
        setIsEdit(false);
        setName("");
        setSite(availableSites[0]);
        setUsers([]);
        setCurrentGroup({});
        setIsDefault(false)
    }

    const handleAddOrEditClick = () => {
        isEdit ? updateGroup() : AddGroup();
    }

    return (
        <div>
            <div className="admin-header">
                <h2>Groups Manager</h2>
                <div className="form-inline">
                    <button type="button" className="btn btn-primary" data-toggle="modal" data-target="#exampleModalCenter" onClick={handleAddClick} >
                        Add
                    </button>
                </div>
            </div>
            <AddEditModal
                availableSites={availableSites}
                currentSiteUsers={currentSiteUsers}
                name={name}
                setName={setName}
                site={site}
                setSite={setSite}
                users={users}
                setUsers={setUsers}
                isEdit={isEdit}
                handleAddOrEditClick={handleAddOrEditClick}
                isDefault={isDefault}
            />
            <Table groups={groups} handleEditClick={handleEditClick} DeleteGroup={DeleteGroup} />
            <ToastsContainer store={ToastsStore} />
        </div>
    );
}
