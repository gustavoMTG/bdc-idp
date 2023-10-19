$("button").on("click", function() {

  $("h1").text("Has sido hackeado.")
  setTimeout(function() {$("h1").text("Nah joda ;)")}, 3000);

});