from bugbug import bugzilla, phabricator, repository, db

def download_datasets():
    """Download necessary datasets"""
    datasets = [
        (bugzilla.BUGS_DB, "Bugs"),
        (phabricator.REVISIONS_DB, "Phabricator Revisions"), 
        (repository.COMMITS_DB, "Repository Commits")
    ]
    
    for dataset, name in datasets:
        db.download(dataset)

if __name__ == "__main__":
    download_datasets()
