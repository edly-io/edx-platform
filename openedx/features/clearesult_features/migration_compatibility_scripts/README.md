# Overview
This folder contains some compatibity scripts. Clearesuult team provides some
user activity files which are related to some other LMS. The format of the file
is not compatible with openedx. So these scripts are there for making those files
compatible.

## How to use compatibility script
1. Place your activity file in the current directory. The name of the file should be `original_user_activity_file.csv`. The content of the file will be like the sample file given in the folder named `sample_user_activity_with_temporary_emails.csv`
2. run the script using command `python3 compatibility_script.py`. It will create a new file for you named `courses.csv`
3. Fill out the `Course ID` information in that file
4. Run the script again and there will be a new file named `new_user_activity.csv`. This file will be compatible with openedx
