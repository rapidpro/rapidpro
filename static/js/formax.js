(function() {
    var _bindToggle, _initializeForm, _submitFormax, hideSection, showSection;
  
    showSection = function(section) {
      var ie;
      ie = section.parents("html").hasClass("ie");
      if (section.data("readonly")) {
        return;
      }
      if (ie || section.data("action") === 'fixed') {
        section.find(".formax-form").show();
        return section.find(".formax-icon").css({
          "font-size": "80px",
          width: "80px",
          height: "80px"
        });
      } else {
        return section[0].classList.add("open");
      }
    };
  
  
    /*
    Manually contract an expandable section
     */
  
    hideSection = function(section) {
      var ie;
      if (section.data("action") === 'fixed') {
        return;
      }
      ie = section.parents("html").hasClass("ie");
      if (ie) {
        section.find(".formax-summary").show();
        return section.find(".formax-form").hide();
      } else {
        section[0].classList.remove("open");
        return section.find(".formax-summary").show();
      }
    };
  
  
    /*
    Fetches new data for the given expandable section.
    Note this will take care of binding all dynamic functions.
     */
  
    window.fetchData = function(section) {
      var url;
      if (section.data("href")) {
        url = section.data('href');
        return fetchPJAXContent(url, "#" + section.attr("id") + " > .formax-container", {
          headers: {
            "X-FORMAX": true
          },
          onSuccess: function() {
            section.data("loaded", true);
            _initializeForm(section);
            if (section.data("fixed")) {
              showSection(section);
            } else {
              _bindToggle(section.find(".formax-icon"));
            }
            return section.show();
          }
        });
      } else {
        return section.data("loaded", true);
      }
    };
  
    _initializeForm = function(section) {
      var action, buttonName, form, onLoad;
      action = section.data('action');
      form = section.find("form");
      if (action === 'formax' || action === 'redirect' || action === 'open') {
        buttonName = section.data("button");
        if (!buttonName) {
          buttonName = gettext("Save");
        }
        form.off("submit").on("submit", _submitFormax);
        if (!section.data("nobutton")) {
          form.append("<input type=\"submit\" class=\"button-primary\" value=\"" + buttonName + "\"/>");
          form.find(".form-actions").remove();
        }
        form.find(".submit-button").on("click", function() {
          return $(this).addClass("disabled").attr("enabled", false);
        });
        onLoad = section.data("onload");
        if (onLoad) {
          eval_(onLoad)();
        }
        if (!section.data("fixed")) {
          _bindToggle(section.find(".formax-summary"));
        }
        if (action === 'open') {
          showSection(section);
          window.scrollTo(0, section.offset().top);
        }
      }
      if (action === 'fixed') {
        return form.attr("action", section.data("href"));
      }
    };
  
    _submitFormax = function(e) {
      var followRedirects, form, section;
      e.preventDefault();
      form = $(this);
      section = form.parents(".formax-section");
      followRedirects = section.data("action") === 'redirect';
      return fetchPJAXContent(section.data("href"), "#" + section.attr("id") + " > .formax-container", {
        postData: form.serialize(),
        headers: {
          "X-FORMAX": true
        },
        followRedirects: followRedirects,
        onSuccess: function() {
          var dependents, formax_form;
          _initializeForm(section);
          formax_form = section.find(".formax-form");
          if (formax_form.hasClass("errors")) {
            section.find(".formax-summary").hide();
            formax_form.show();
          } else {
            if (section.data("action") !== 'fixed') {
              hideSection(section);
            }
          }
          dependents = section.data("dependents");
          if (dependents) {
            return $("#id-" + dependents).each(function() {
              return fetchData($(this));
            });
          }
        }
      });
    };
  
    _bindToggle = function(bindTo) {
      var action, section;
      section = bindTo.parents(".formax-section");
      action = section.data('action');
      if (action === 'fixed') {
        return showSection(section);
      } else if (action === 'formax' || action === 'redirect' || action === 'open') {
        return bindTo.off("click").on("click", function() {
          section = $(this);
          if (!bindTo.tagName !== "formax") {
            section = bindTo.parents(".formax-section");
          }
          $(".formax > .formax-section").each(function() {
            if ($(this).attr("id") !== section.attr("id")) {
              return hideSection($(this));
            }
          });
          if (section[0].classList.contains("open")) {
            return hideSection(section);
          } else {
            return showSection(section);
          }
        });
      } else if (action === 'link') {
        return bindTo.off("click").on("click", function() {
          return document.location.href = section.data('href');
        });
      }
    };
  
    $(function() {
      $('.formax-section .formax-summary').each(function() {
        var section;
        section = $(this);
        return _bindToggle(section);
      });
      $('.formax .formax-section').each(function() {
        var section;
        section = $(this);
        return _initializeForm(section);
      });
      return $('.formax-section .formax-icon').each(function() {
        var section;
        section = $(this);
        return _bindToggle(section);
      });
    });
  
  }).call(this);