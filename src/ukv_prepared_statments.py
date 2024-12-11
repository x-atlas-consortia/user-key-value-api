# Set up scalars with SQL strings matching the paramstyle of the database module, as
# specified at https://peps.python.org/pep-0249
#
# Using the "format" paramstyle with mysql-connector-python module for MySQL 8.0+
#
# Ignore threat of unsanitized user input for SQL injection, XSS, etc. due to current
# nature of site at AWS, modification requests come from Globus authenticated users,
# microservice rejection of non-valid JSON, microservice use of prepared statements,
# MySQL rejection of non-valid JSON as KEY_VALUE, etc.
SQL_UPSERT_USERID_KEY_VALUE = \
    ("INSERT INTO user_key_value"
     " (GLOBUS_IDENTITY_ID, KEY_NAME, KEY_VALUE, UPSERT_UTC_TIME)"
     " VALUES"
     " (%s, %s, %s, NOW()) AS ukv"
     " ON DUPLICATE KEY UPDATE"
     " GLOBUS_IDENTITY_ID=ukv.GLOBUS_IDENTITY_ID"
     " ,KEY_NAME=ukv.KEY_NAME"
     " ,KEY_VALUE=ukv.KEY_VALUE"
     " ,UPSERT_UTC_TIME=NOW()"
     )

SQL_SELECT_USERID_KEY_VALUE = \
    ("SELECT GLOBUS_IDENTITY_ID AS globus_id"
     "       ,KEY_NAME AS keyname"
     "       ,KEY_VALUE AS keyvalue"
     " FROM user_key_value"
     " WHERE GLOBUS_IDENTITY_ID = %s"
     "   AND KEY_NAME = %s"
     )

SQL_SELECT_USERID_ALL = \
    ("SELECT GLOBUS_IDENTITY_ID AS globus_id"
     "       ,KEY_NAME AS keyname"
     "       ,KEY_VALUE AS keyvalue"
     " FROM user_key_value"
     " WHERE GLOBUS_IDENTITY_ID = %s"
     )

SQL_DELETE_USERID_KEY_VALUE = \
    ("DELETE FROM user_key_value"
     " WHERE GLOBUS_IDENTITY_ID = %s"
     "   AND KEY_NAME = %s"
     )

# Below are strings (rather than scalars) which must be developed into
# prepared statements due to needing a variable number of placeholders.
SQL_SELECT_USERID_NAMED_KEY_VALUES_str =    "SELECT GLOBUS_IDENTITY_ID AS globus_id" \
                                            "       ,KEY_NAME AS keyname" \
                                            "       ,KEY_VALUE AS keyvalue" \
                                            " FROM user_key_value" \
                                            " WHERE GLOBUS_IDENTITY_ID = %s" \
                                            "   AND KEY_NAME IN (generated_placeholders_for_named_keys)"

SQL_UPSERT_USERID_KEY_VALUES_str =  "INSERT INTO user_key_value" \
                                    " (GLOBUS_IDENTITY_ID, KEY_NAME, KEY_VALUE, UPSERT_UTC_TIME)" \
                                    " VALUES" \
                                    " generated_placeholders_for_new_tuples AS ukv" \
                                    " ON DUPLICATE KEY UPDATE" \
                                    " GLOBUS_IDENTITY_ID=ukv.GLOBUS_IDENTITY_ID" \
                                    " ,KEY_NAME=ukv.KEY_NAME" \
                                    " ,KEY_VALUE=ukv.KEY_VALUE" \
                                    " ,UPSERT_UTC_TIME=NOW()"
