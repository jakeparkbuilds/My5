# AWS Lambda handlers for the simulation worker and DLQ processor.
# These are thin invocation shells that call the same core functions
# used by the local polling loop — handle_job and job_store.fail_job.
# No simulation logic lives here.
