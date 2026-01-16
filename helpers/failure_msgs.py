failure_msgs = {
    None: {
        "page_title": "Task Failure",
        "section_title": "Could Not Add Permit to Cart",
        "section_text": "The requested permit could not be added to cart."
                        " This may be due to a server error, or due to the permit going out of stock."
                        " <strong>You may still have a chance of getting this permit if you quickly go"
                        "<a href='https://www.recreation.gov/permits/445860/registration/detailed-availability"
                        "'>here</a> and attempt to manually reserve.</strong>"
    },
    "token": {
        "page_title": "Token Validation Error",
        "section_title": "Token Validation Error",
        "section_text": "The provided ATC token is invalid. Either the token has been manipulated, or it's"
                        " expiration time has been reached."
    },
    "missing_cred": {
        "page_title": "Missing Credential Error",
        "section_title": "Missing Account Credentials",
        "section_text": "You have not configured this service with your recreation.gov account credentials,"
                        " and the server administrator"
                        " has not enabled backup credential mode. Please proceed <a href='/'>here</a>"
                        " to configure your rec.gov credentials and try again."
    },
    "invalid_cred": {
        "page_title": "Invalid Credential Error",
        "section_title": "Invalid Account Credentials",
        "section_text": "The provided recreation.gov credentials are invalid. Please proceed <a href='/'>here</a> to"
                        " re-enter your credentials and try again."
    },
    "duplicate": {
        "page_title": "Duplicate Task",
        "section_title": "Duplicate Task Requested",
        "section_text": "The requested task has been flagged as a duplicate. This generally means that"
                        " another user has already requested a permit for these dates. Tasks are marked as"
                        " duplicates for 5 minutes after succeeding, and during processing. <strong>In the"
                        " case that this task already succeeded, you may proceed"
                        " <a href='/atc/success'>here</a> to complete checkout.</strong>"
    }

}
