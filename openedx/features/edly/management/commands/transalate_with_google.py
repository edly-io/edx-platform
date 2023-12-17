"""
Translate .po files using the Google Translation API and optionally export translations to CSV.

Usage:
1. First, make sure to set the environment variable GOOGLE_APPLICATION_CREDENTIALS to the path of your Google Cloud
 service account key.

2. To translate the .po files and update them with the translations:
   python translate_with_google.py LANGUAGE_CODE

3. To translate the .po files and generate CSV files:
   python translate_with_google.py LANGUAGE_CODE --csv --/path/to/csv

Please ensure you have google-cloud-translate==2.0.1
"""
# pylint: disable=E0401
import argparse
import csv
import logging
import os

import polib
from google.cloud import translate_v2

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "path_to_your_.json_credential_file"

COMMENT = "Translated from Google."
client_instance = translate_v2.Client()


def write_to_csv(path, msgid, msgstr, source):
    """
      Write the translated strings along with the source to a CSV file.

      Parameters:
          path (str): The path to the CSV file.
          msgid (str): The original source string (msgid) to be translated.
          msgstr (str): The translated string (msgstr) for the source.
          source (str): The source of the translation (e.g., "Google Translation" or "Transifex").
      """
    try:
        with open(path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([msgid, msgstr, source])

    except (IOError, FileNotFoundError, csv.Error) as error:
        logging.error("An error occurred while writing to CSV: %s", error)


def call_google_translation(text, target_language):
    """
    Call google translation api with help of google cloud library.
    Parameters:
        text (str): The text to be translated.
        target_language (str): The target language code (e.g., 'id', 'ar', etc.).

    Returns:
        str: The translated text.
    """
    try:
        result = client_instance.translate(
            text,
            target_language
        )
        return result["translatedText"]

    except ValueError:
        logging.error("API Error for msgid: %s", text)


def translate_with_google(po_file, target_language):
    """
    Translate the .po file using the Google Translation API and update the .po entries.

    Parameters:
        po_file (str): The path to the .po file to be translated.
        target_language (str): The target language code (e.g., 'id', 'ar', etc.).
    """
    pofile = polib.pofile(po_file, encoding='utf-8')

    for entry in pofile:
        try:
            if not entry.msgstr:
                has_plural = entry.msgid_plural is not None and entry.msgid_plural != ''
                if not has_plural:
                    entry.msgstr = call_google_translation(entry.msgid, target_language)

                else:
                    if 0 not in entry.msgstr_plural.keys():
                        entry.msgstr_plural[0] = call_google_translation(entry.msgid, target_language)
                    if 1 not in entry.msgstr_plural.keys():
                        entry.msgstr_plural[1] = call_google_translation(entry.msgid_plural, target_language)

                entry.comment = COMMENT
                pofile.save()

        except (ValueError, IOError) as error:
            logging.error("Error '%s' while writing to .po file for msgid: '%s'.", error, entry.msgid)

    logging.info("Translation completed for %s.", target_language)


def po_file_to_csv(csv_file_path, po_file_path):
    """
    Export translations from the .po file to a CSV file.

    Parameters:
        csv_file_path (str): The path to the CSV file.
        po_file_path (str): The path to the .po file.
    """

    with open(csv_file_path, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['String', 'Translated String', 'Source'])

    pofile = polib.pofile(po_file_path, encoding='utf-8')
    for entry in pofile:
        has_plural = entry.msgid_plural is not None and entry.msgid_plural != ''

        if entry.comment == COMMENT:
            source = "Google Translation"
        else:
            source = "Transifex"

        if not has_plural:
            write_to_csv(csv_file_path, entry.msgid, entry.msgstr, source)
        else:
            write_to_csv(csv_file_path, entry.msgid, entry.msgstr_plural[0], source)
            write_to_csv(csv_file_path, entry.msgid_plural, entry.msgstr_plural[1], source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translate .po files and export translations to CSV.")
    parser.add_argument("language_code", help="Language code (e.g., 'id', 'ar', etc.)")
    parser.add_argument("--csv", action="store_true", help="Optional: Generate CSV file")
    parser.add_argument("--csv_path", help="Optional: Path to export CSV file")
    args = parser.parse_args()

    language_code = args.language_code
    csv_path = args.csv_path or ""

    file = 'django'
    file_path = "../../../conf/locale/%s/LC_MESSAGES/%s.po" % (language_code, file)
    translate_with_google(file_path, language_code)
    if args.csv:
        po_file_to_csv("%sdjango_po.csv" % csv_path, file_path)

    file = 'djangojs'
    file_path = "../../../../../conf/locale/%s/LC_MESSAGES/%s.po" % (language_code, file)
    translate_with_google(file_path, language_code)
    if args.csv:
        po_file_to_csv("%sdjangojs_po.csv" % csv_path, file_path)
