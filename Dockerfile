#This image gets pushed into dockerhub as istresearch/p4-engage:code-4.0.0-a3-dev
FROM rapidpro/rapidpro-base:v4

WORKDIR /rapidpro

ADD . /rapidpro
 
