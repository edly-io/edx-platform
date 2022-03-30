import React, {useState, useEffect} from 'react';
import { ToastsContainer, ToastsStore } from "react-toasts";
import Cookies from "js-cookie";
import Table from './components/Table';
import AddEditModal from './components/AddEditModal';
import HttpClient from "../../continuing_education/js/client";




export default function CatalogsContent({context}) {
    // all states
    const [availableCatalogs, setAvailableCatalogs] = useState([]);
    const [isEditButton, setIsEditButton] = useState(false);
    const [catalogName, setCatalogName] = useState("");
    const [currentCatalog, setCurrentCatalog] = useState({});
    // will be used for super user
    const [isPublicAdded, setIsPublicAdded] = useState(false);
    const [availableSites, setAvailableSites] = useState([]);
    const [selectedSite, setSelectedSite] = useState({});
    // will fetch the all available courses in one API call
    // store them in availableCourses
    const [availableCourses, setAvailableCourses] = useState([]);
    // if user apply some filteration on available courses
    // store the filtered result in availableFilteredCourses
    const [availableFilteredCourses, setAvailableFilteredCourses] = useState([]);
    const [selectedCourses, setSelectedCourses] = useState([]);

    // http client to perform API requests
    const client = new HttpClient({
        headers: {
            "X-CSRFToken": Cookies.get("csrftoken"),
        },
    });

    // all the fucntions performing API calls
    const getAvailableCatalogs = async () => {
        let data = (await client.get(context.CATALOGS_URL)).data;
        setAvailableCatalogs(data);
    }

    const getAvailableSites = async () => {
        let data = (await client.get(context.SITES_URL)).data;
        setAvailableSites(data);
    }

    const getAvailableCourses = async () => {
        let data = (await client.get(context.COURSES_URL)).data;
        setAvailableCourses(data);
    }

    const createOrUpdateCatalog = async (params) => {
        if (!isEditButton) {
            if (params.site.id == 0) {
                params.site = {};
            }
            try {
                let responseData = (await client.post(context.CATALOGS_URL, params)).data;
                setAvailableCatalogs([...availableCatalogs, responseData]);
                setCurrentCatalog(responseData);
                ToastsStore.success('Success: Catalog has been added.');
                setIsEditButton(true);
            } catch (ex) {
                ToastsStore.error('Failed: Failed to add catalog.');
            }
        } else {
            try {
                let responseData = (await client.patch(context.CATALOGS_URL + currentCatalog.id.toString(), params)).data;
                setCurrentCatalog(responseData);
                const updatedCatalogs = availableCatalogs.filter((availableCatalog) => availableCatalog.id != currentCatalog.id)
                setAvailableCatalogs([...updatedCatalogs, responseData])
                ToastsStore.success('Updated: Catalog has been updated.');
            } catch (ex) {
                ToastsStore.error('Failed: Failure while updating .');
            }
        }
    }

    const handleDeleteCatalogButton = async (pk) => {
        if (confirm('Are you sure to delete the catalog with id: ' + pk)) {
            try {
                let data = (await client.delete(context.CATALOGS_URL + pk)).data
                const newCatalogs = availableCatalogs.filter((availableCatalog) => availableCatalog.id != pk);
                setAvailableCatalogs(newCatalogs);
                ToastsStore.success("Success: The requested catalog has been deleted successfully.");
            } catch (ex) {
                ToastsStore.error('Failed: Could not delete the requested catalog.');
            }
        }
    }

    const loadInitialData = () => {
        getAvailableCatalogs();
        getAvailableSites();
        getAvailableCourses();
    }

    useEffect(() => {
        loadInitialData();
    }, [])


    const handleAddCatalogButton = (event) => {
        // perform the necessary fetching
        if (!isPublicAdded && context.IS_SUPERUSER == 1) {
            setAvailableSites([...availableSites, { id: 0, domain: "public"}])
            setIsPublicAdded(true);
        }
        setCatalogName("");
        setIsEditButton(false);
        // by default for super user the site will be public
        if (context.IS_SUPERUSER == 1) {
            setSelectedSite({id:0, domain:"public"});
            filterCoursesPerSelectedSite(0);
        } else {
            if (availableSites.length > 0) {
                setSelectedSite(availableSites[0]);
                filterCoursesPerSelectedSite(availableSites[0].id);
            }
        }
        setSelectedCourses([]);
    }

    const handleEditCatalogButton = (id) => {
        // perform the necessary fetching
        // will be sent in table
        let catalog = availableCatalogs.filter((availableCatalog) => availableCatalog.id == id)[0];
        setCurrentCatalog(catalog);
        if (catalog.site == null) {
            setSelectedSite({id: 0, domain:"public"});
            filterCoursesPerSelectedSite(0);
        } else {
            setSelectedSite(catalog.site);
            filterCoursesPerSelectedSite(catalog.site.id);
        }
        setCatalogName(catalog.name);
        setSelectedCourses(catalog.clearesult_courses.map(clearesult_course => clearesult_course.course_id));
        setIsEditButton(true);
    }

    const filterCoursesPerSelectedSite = (siteId) => {
        var filteredData = availableCourses.filter((availableCourse) => (availableCourse.site == null || (availableCourse.site.id == siteId)))
        setAvailableFilteredCourses(filteredData);
    }

    const handleSiteSelect = (event) => {
        // now you have selected site
        // and all available valid courses
        // and now filter out courses from these courses
        // using that site
        setSelectedSite({
            id: parseInt(event.target.value),
            domain: event.target.options[event.target.selectedIndex].text
        });
        filterCoursesPerSelectedSite(event.target.value);
    }

    const handleSubmit = (event) => {
        event.preventDefault();
        // now you have all the values you can perform the
        // update / create stuff based on the value of isEditButton

        // dual list box will don't do anything with the availableCourses
        // it will remain as it is but make sure that you update the value of
        // selectedCourse
        let params = {
            "name": catalogName,
            "clearesult_courses": selectedCourses,
            "site": selectedSite
        }
        createOrUpdateCatalog(params);
    }

    return (
        <div className="catalogs-manager">
            <div className="admin-header">
                <h2>Catalogs Manager</h2>
                <div className="form-inline">
                    <button
                        type="button"
                        className="btn btn-primary"
                        data-toggle="modal"
                        data-target="#exampleModalCenter"
                        onClick={handleAddCatalogButton}
                    >
                        Add
                    </button>
                </div>
            </div>

            <AddEditModal
                catalogName={catalogName}
                availableSites={availableSites}
                availableFilteredCourses={availableFilteredCourses}
                selectedCourses={selectedCourses}
                handleSubmit={handleSubmit}
                isEditButton={isEditButton}
                setCatalogName={setCatalogName}
                handleSiteSelect={handleSiteSelect}
                setSelectedCourses={setSelectedCourses}
                selectedSite={selectedSite}
            />
            <Table
                availableCatalogs={availableCatalogs}
                handleDeleteCatalogButton={handleDeleteCatalogButton}
                handleEditCatalogButton={handleEditCatalogButton}
                isSuperUser={context.IS_SUPERUSER}
            />
            <ToastsContainer store={ToastsStore} />
        </div>
    )
}
