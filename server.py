from flask import Flask, render_template, url_for, redirect, send_file, request
from flask_wtf import FlaskForm
from wtforms import StringField, DateField, SubmitField, BooleanField, RadioField, TextAreaField, SelectMultipleField, \
    TimeField, SelectField, DecimalField, DecimalRangeField, FieldList
from wtforms.validators import DataRequired
from flask_bootstrap import Bootstrap5
from pipylib import get_converted_alarms, generate_table, batch_request, batch_signals_segmentation
# from flask_sqlalchemy import SQLAlchemy
import psycopg2 as pg2
from psycopg2 import extras
import os

# Get the directory of the current script
script_dir = os.path.dirname(os.path.realpath(__file__))

# Change the working directory to the script's directory
os.chdir(script_dir)

# Initialize web app
app = Flask(__name__)
app.secret_key = "dev"
# Configure connection to Postgres data base in case of using SQL Alchemy
# app.config["SQLALCHEMY_DATABASE_URI"] = 'postgresql://postgres:1996@localhost/idp'


# Initialize styling with bootstrap
# This line of code replaces adding the bootstrap CDN to HTML
bootstrap = Bootstrap5(app)


# Define Database queries function
def query_db(stmnt: str):
    """
    This function makes SQL queries easier from POSTGRE
    :param stmnt: This is the SQL statement.
    :return: Queried data
    """
    with pg2.connect(database="idp", user="postgres", password="1996") as conn:
        with conn.cursor(cursor_factory=extras.NamedTupleCursor) as cursor:
            cursor.execute(stmnt)
            query = cursor.fetchall()
    return query


# Each form is defined as a class that inherits from class FlaskForm
class EventForm(FlaskForm):
    stations = StringField("Estaciones", validators=[DataRequired()])
    start_date = DateField("Fecha de inicio", validators=[DataRequired()])
    end_date = DateField("Fecha de fin", validators=[DataRequired()])
    submit = SubmitField()


# Landing page, if methods argument is not specified each page only supports GET requests
@app.route('/', methods=["GET", "POST"])
def home():
    return render_template("index.html")


# In a future for FPL
@app.route('/fpl')
def fpl():
    return render_template("fpl.html")


# Route to display alarms, at the end of the URL a variable types_ is included as argument
@app.route('/alarms/<types_>')
def alarms(types_):
    """
    Display alarms by code.
    :param types_: An alarm or list of alarms codes separated by _
    :return: Nothing
    """
    types_ = types_.split("_")
    final_list = []
    stations = set()
    for type_ in types_:
        signals = get_converted_alarms(name_filter=f"*_al_{type_}.valor")
        segmented_signals = batch_signals_segmentation(signals)
        for signal_ in segmented_signals:
            batch_request(signal_, end_value=True)
        # end_values = [signal.data for signal in signals]
        # for signal in signals:
        #     signal.get_endvalue()
        end_values = [signal.data for signal in signals]
        for end_value, signal in zip(end_values, signals):
            stations.add(signal.station)
            try:
                if end_value["Name"] == "ON":
                    color = "red"
                    final_list.insert(0, (signal.descriptor, end_value["Name"], color, end_value["Timestamp"]))
                elif end_value["Name"] != "OFF":
                    color = "yellow"
                    final_list.insert(0, (signal.descriptor, end_value["Name"], color, end_value["Timestamp"]))
                else:
                    color = "green"
                    final_list.append((signal.descriptor, end_value["Name"], color, end_value["Timestamp"]))
            except KeyError:
                pass
            except TypeError:
                pass

    return render_template("alarms.html", signals=final_list, stations=stations)


# This route requires POST method because the user has to provide information to retrieve the events
@app.route("/events", methods=["GET", "POST"])
def events():
    form = EventForm()
    if form.validate_on_submit():
        start_date = form.data["start_date"]
        end_date = form.data["end_date"]
        stations = form.data["stations"].split(" ")
        print(f"Request: {stations} from {start_date} to {end_date}")

        signals = []
        for station in stations:
            for caz in ["mv", "ma", "ro", "cb"]:
                for source in ["caz", "scl"]:
                    signals += get_converted_alarms(name_filter=f"{source}*{caz}{station}*valor")

        print(f"Signals length: {len(signals)}")
        df = generate_table(
            signals=signals,
            start_time=start_date,
            end_time=end_date,
            batch=True,
            max_batch=50
        )

        try:
            name = "_".join(stations)
            df.to_excel(f"./static/alarms/{name}.xlsx", index=False)
        except PermissionError:
            pass
        else:
            return send_file(f"./static/alarms/{name}.xlsx", as_attachment=True)
    return render_template("events.html", form=form)


@app.route("/logs")
def logs():
    # POSTGRE query
    events = query_db(
        "SELECT * FROM web_log_form "
        "LIMIT 6"
    )
    ids = [event.idevento for event in events]
    participations = query_db(
        "SELECT * FROM participants_cards "
        f"WHERE idevento IN {tuple(ids)}"
    )

    return render_template("logs.html", events=events, participations=participations)


@app.route("/participant_form/<id>")
def participant_form(id):
    marcas = query_db(
        "SELECT * FROM marca "
        "WHERE marca.enabled = true"
    )
    marcas = [marca.nombre + " " + marca.modelo for marca in marcas]

    class LogForm(FlaskForm):
        brief = StringField("Resumen", validators=[DataRequired()])
        text_area = TextAreaField("Descripción", render_kw={"rows": 15})
        radio = RadioField("select radio", choices=["option1", "option2", "option3"])
        range_field = DecimalRangeField()
        check_boxes = FieldList(BooleanField("option"))
        relays_options = SelectMultipleField("Opciones", choices=marcas)
        date = DateField("Fecha")
        start_time = TimeField("Hora de inicio")
        end_time = TimeField("Hora de fin")
        submit = SubmitField("Enviar")

    form = LogForm()
    for marca in marcas:
        form.check_boxes.append_entry(marca)
    return render_template("participant_form.html", form=form)


@app.route("/display_entry/<id>")
def display_entry(id):
    events = query_db("SELECT * FROM participante ")
    return render_template("display_form.html", events=events)


# This if statement is important because once the app is deployed in a  WSGI server (Web Service Gateway
# Interface) let's say Waitress, the app is ran as a module handling each route decorated by app.route().
# If the if statement wasn't in this file and we simply executed app.run(), both services would collide each other.
# This way we avoid having to run the file from command line when developing/debugging.
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
