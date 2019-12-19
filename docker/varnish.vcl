vcl 4.0;

backend default {
  .host = "BACKEND_ADDRESS";
  .port = "BACKEND_PORT";
}

sub vcl_recv {
    if (req.url ~ "^/sitestatic/") {
        return (hash);
    } else if (req.url ~ "^/api/") {
        return (pipe);
    } else {
        return (pass);
    }
}

sub vcl_backend_response {
    if (bereq.url ~ "^/sitestatic/") {
        set beresp.ttl = 120m;
    } else {
        set beresp.uncacheable = true;
    }
}

sub vcl_deliver {
    unset resp.http.Via;
    unset resp.http.Server;
}

sub vcl_pipe {
    set bereq.http.connection = "close";
}
