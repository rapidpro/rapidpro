# RapidPro (IST Research Fork)

This repository is a fork of the RapidPro library used for developing additional functionality.

### Development

Ensure your changes are always compatible with the latest master release by running the following command to pull the latest image:

```
docker pull rapidpro/rapidpro:master
```

If you are running RapidPro for the first time, please ensure the following variable is set for the rapidpro service:
```
- MANAGEPY_MIGRATE=on
```

It will allow the database to be initialized.

To stand up a development instance, simply run:

```
./dc-rapidpro.dev.sh up --build -d
```

RapidPro should now be available at `0.0.0.0:8000`.


Any local changes will be picked up by the development instance. If, in any case, there are changes that do not appear in the development instance, ensure that the files are properly mounted in `rapidpro-compose.dev.yaml`
