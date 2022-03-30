import io
import logging
import os.path
from csv import Error, DictReader, Sniffer, DictWriter
from os import path
from pprint import pprint

logger = logging.getLogger(__name__)

EMPTY_COURSE_NAME_CODE = 1
DUPLICATE_ENTRY_CODE = 2
COURSE_MISMATCH_CODE = 3
EMPTY_EMAIL_CODE = 4
VALID_ENTRY_CODE = 0


def get_file_encoding(file_path):
    """
    Returns the file encoding format.
    Arguments:
        file_path (str): Path of the file whose encoding format will be returned
    Returns:
        encoding (str): encoding format e.g: utf-8, utf-16, returns None if doesn't find
                        any encoding format
    """
    try:
        file = io.open(file_path, 'r', encoding='utf-8')
        encoding = None
        try:
            _ = file.read()
            encoding = 'utf-8'
        except UnicodeDecodeError:
            file.close()
            file = io.open(file_path, 'r', encoding='utf-16')
            try:
                _ = file.read()
                encoding = 'utf-16'
            except UnicodeDecodeError:
                logger.exception('The file encoding format must be utf-8 or utf-16.')

        file.close()
        return encoding

    except IOError as error:
        logger.exception('({}) --- {}'.format(error.filename, error.strerror))
        return None


def get_csv_file_control(file_path):
    """
    Returns opened file and dict_reader object of the given file path.
    """
    csv_file = None
    dialect = None
    try:
        encoding = get_file_encoding(file_path)
        if not encoding:
            logger.exception('Because of invlid file encoding format, user creation process is aborted.')
            return

        csv_file = io.open(file_path, 'r', encoding=encoding)
        try:
            dialect = Sniffer().sniff(csv_file.readline())
        except Error:
            logger.exception('Could not determine delimiter in the file.')
            csv_file.close()
            return

        csv_file.seek(0)
    except IOError as error:
        logger.exception('({}) --- {}'.format(error.filename, error.strerror))
        return

    dict_reader = DictReader(csv_file, delimiter=dialect.delimiter if dialect else ',')
    csv_reader = (dict((k.strip(), v.strip() if v else v) for k, v in row.items()) for row in dict_reader)

    return {'csv_file': csv_file, 'csv_reader': csv_reader}


def is_eligible_for_entry(output_rows, courses_mapping, new_row):
    if not courses_mapping.get(new_row.get('Course')):
        return COURSE_MISMATCH_CODE

    for row in output_rows:
        if row.get('Email') == new_row.get('Email') and row.get('Course') == new_row.get('Course'):
            return DUPLICATE_ENTRY_CODE
        elif new_row.get('Course') == '':
            # No usage of this code as this case
            # would have alredy been handled in
            # course mismatch check
            return EMPTY_COURSE_NAME_CODE
        elif new_row.get('Email') == '':
            return EMPTY_EMAIL_CODE

    return VALID_ENTRY_CODE


def clean_course_name(course_name):
    return course_name.split('(')[0].strip()


def clean_email(email):
    return email.lower()


def clean_row(row):
    row['Course'] = clean_course_name(row.get('Course'))
    row['Email'] = clean_email(row.get('Email'))


def do_add(my_set, element):
    length_before_addition = len(my_set)
    my_set.add(element)
    return len(my_set) != length_before_addition


def get_unique_courses(file_path):
    file_controller = get_csv_file_control(file_path)
    courses = set()
    total_count = 0
    empty_course_names_count = 0

    for row in file_controller['csv_reader']:
        total_count += 1
        clean_row(row)
        if row.get('Course'):
            do_add(courses, row.get('Course'))
        else:
            empty_course_names_count += 1

    report = {
        'total_courses': total_count,
        'empty_course_names_count': empty_course_names_count,
        'unique_course_count': len(courses)
    }

    file_controller['csv_reader'].close()
    return courses, report


def print_courses_report(report):
    print('Total number of course names traversed :{}'.format(report['total_courses']))
    print('Total number of courses having empty string :{}'.format(report['empty_course_names_count']))
    print('Total number of unique courses :{}'.format(report['unique_course_count']))


def print_migration_file_report(report):
    print('Total number of rows processed :{}'.format(report.get('total_count')))
    print('Total number of valid unique entries :{}'.format(report.get('valid_row_count')))
    print('Total number of rows discarded :{}'.format(report.get('total_count') - report.get('valid_row_count')))
    print('Total number of rows whose course not found in course file :{}'.format(report.get('course_mismatch_count')))
    print('Total number of duplicate entries which were discarded :{}'.format(report.get('duplicate_entry_count')))
    # This case would have already been handled in couse mismatch message
    # print('Total number of entries discarded because of empty course :{}'.format(report.get('empty_course_count')))
    print('Total number of entries discarded because of empty emails :{}'.format(report.get('empty_email_count')))


def create_new_file_data_for_migration(file_path, courses_mapping):
    file_controller = get_csv_file_control(file_path)
    valid_row_count = 0
    empty_course_count = 0
    empty_email_count = 0
    duplicate_entry_count = 0
    course_mismatch_count = 0
    total_count = 0
    output_rows = []
    for row in file_controller['csv_reader']:
        total_count += 1
        clean_row(row)
        code = is_eligible_for_entry(output_rows, courses_mapping, row)

        if code == VALID_ENTRY_CODE:
            valid_row_count += 1
            output_rows.append(
                {
                    'Number': valid_row_count,
                    'Email': row.get('Email'),
                    'Course': row.get('Course'),
                    'Course ID': courses_mapping.get(row.get('Course'))
                }
            )
        elif code == EMPTY_COURSE_NAME_CODE:
            empty_course_count += 1
        elif code == EMPTY_EMAIL_CODE:
            empty_email_count += 1
        elif code == DUPLICATE_ENTRY_CODE:
            duplicate_entry_count += 1
        elif code == COURSE_MISMATCH_CODE:
            course_mismatch_count += 1
        else:
            pass

    file_controller['csv_reader'].close()
    report = {
        'total_count': total_count,
        'valid_row_count': valid_row_count,
        'empty_course_count': empty_course_count,
        'duplicate_entry_count': duplicate_entry_count,
        'course_mismatch_count': course_mismatch_count,
        'empty_email_count': empty_email_count
    }
    return output_rows, report


def display_courses(courses):
    j = 1
    print('================== Course Names ==================')
    for course in courses:
        print(j, ' ', course)
        j += 1
    print('==================================================')


def write_status_on_csv_file(file_path, output_file_rows):
    """
    Writes the output data on the given file.
    """
    try:
        with open(file_path, 'w') as csv_file:
            if output_file_rows:
                writer = DictWriter(csv_file, fieldnames=output_file_rows[0].keys())
                writer.writeheader()
                for row in output_file_rows:
                    writer.writerow(row)
    except IOError as error:
        logger.exception('(file_path) --- {}'.format(file_path, error.strerror))



def make_output_rows_for_courses_file(courses):
    courses_output_rows = []
    j = 1
    for course in courses:
        courses_output_rows.append(
            {
                'Number': j,
                'Course': course,
                'Course ID': ''
            }
        )
        j += 1
    return courses_output_rows


def validate_courses_file(file_path):
    file_controller = get_csv_file_control(file_path)
    courses_mapping = {}

    for row in file_controller['csv_reader']:
        if not row.get('Course ID'):
            return None
        courses_mapping.update(
            {
                row.get('Course'): row.get('Course ID')
            }
        )

    file_controller['csv_reader'].close()
    return courses_mapping


def main():
    activity_file_path = 'original_user_activity.csv'
    courses_file_path = 'courses.csv'
    new_activity_file_path = 'new_user_activity.csv'

    if not path.exists(activity_file_path):
        print(activity_file_path, ' does not exist.')

    if not path.exists(courses_file_path):
        print('Creating courses file')
        courses, report = get_unique_courses(activity_file_path)
        courses = sorted(courses)
        display_courses(courses)
        print_courses_report(report)
        courses_output_rows = make_output_rows_for_courses_file(courses)
        write_status_on_csv_file(courses_file_path, courses_output_rows)
    else:
        print(courses_file_path, ' already exists.')
        courses_mapping = validate_courses_file(courses_file_path)
        if not courses_mapping:
            print('Please provide all Course IDs.')
            return
        output_rows, report = create_new_file_data_for_migration(activity_file_path, courses_mapping)
        write_status_on_csv_file(new_activity_file_path, output_rows)
        print_migration_file_report(report)


main()
