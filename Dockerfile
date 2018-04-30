FROM rapidpro/rapidpro:v3.0.334

WORKDIR /rapidpro

COPY . /rapidpro/

CMD ["/startup.sh"]