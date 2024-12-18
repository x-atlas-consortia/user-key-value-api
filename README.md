# User Key-Value Store API

The ukv-api is a RESTful web service used to store and query values by user and key. For all endpoints a standard auth header is required
```
Authorization: Bearer <HuBMAP Globus Groups Token>
```

The user's identity will be retrieved from the auth token to identify who's key(s) will be retrieved or managed by the various endpoints described in the Smart API spec.

## Docker build for local/DEV development

There are a few configurable environment variables to keep in mind:

- `HOST_UID`: the user id on the host machine to be mapped to the container. Default to 1001 if not set or null.
- `HOST_GID`: the user's group id on the host machine to be mapped to the container. Default to 1001 if not set or null.

```
cd docker
./docker-development.sh [check|config|build|start|stop|down]
```

## Docker build for deployment on TEST/PROD

```
cd docker
./docker-deployment.sh [start|stop|down]
```

## Development process

### To release via TEST infrastructure
- Make new feature or bug fix branches from `main` branch (the default branch)
- Make PRs to `main`
- As a codeowner, Zhou (github username `yuanzhou`) is automatically assigned as a reviewer to each PR. When all other reviewers have approved, he will approve as well, merge to TEST infrastructure, and redeploy the TEST instance.
- Developer or someone on the team who is familiar with the change will test/qa the change
- When any current changes in the `main` have been approved after test/qa on TEST, Zhou will release to PROD using the same docker image that has been tested on TEST infrastructure.

### To work on features in the development environment before ready for testing and releasing
- Make new feature branches off the `main` branch
- Make PRs to `dev-integrate`
- As a codeowner, Zhou is automatically assigned as a reviewer to each PR. When all other reviewers have approved, he will approve as well, merge to devel, and redeploy the DEV instance.
- When a feature branch is ready for testing and release, make a PR to `main` for deployment and testing on the TEST infrastructure as above.

### Updating API Documentation

The documentation for the API calls is hosted on SmartAPI. Modifying the `user-key-value-api-spec.yaml` file and commititng the changes to github should update the API shown on SmartAPI. SmartAPI allows users to register API documents.  The documentation is associated with this github account: api-developers@hubmapconsortium.org.
