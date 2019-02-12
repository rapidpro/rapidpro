FROM rapidpro/rapidpro:v4.14.0

WORKDIR /rapidpro

COPY . /rapidpro/

CMD ["/startup.sh"]