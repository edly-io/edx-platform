"""
Helper functions for clearesult_features app.
"""
import io
import logging
from csv import Error, DictReader, Sniffer

logger = logging.getLogger(__name__)


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
