# views.py
from pathlib import Path
from collections import deque
from jinja2 import Environment, FileSystemLoader
import yaml


from flask import (
    Blueprint,
    Response,
    json,
    redirect,
    render_template,
    session,
    url_for,
)
from flask_wtf import FlaskForm
from wtforms.fields import (
    BooleanField,
    HiddenField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    FormField,
)
from wtforms.validators import InputRequired, NumberRange

from jobbergate.lib import config, fullpath_import
from jobbergate import appform

main_blueprint = Blueprint("main", __name__, template_folder="templates")


def parse_field(form, field, render_kw=None):
    if isinstance(field, appform.Text):
        setattr(
            form,
            field.variablename,
            StringField(
                field.message,
                validators=[InputRequired()],
                default=field.default,
                render_kw=render_kw,
            ),
        )
    if isinstance(field, appform.Integer):
        setattr(
            form,
            field.variablename,
            IntegerField(
                field.message,
                default=field.default,
                validators=[
                    InputRequired(),
                    NumberRange(min=field.minval, max=field.maxval),
                ],
                render_kw=render_kw,
            ),
        )
    if isinstance(field, appform.List):
        choices = []
        for choice in field.choices:
            if not isinstance(choice, tuple):
                choices.append((str(choice), str(choice)))
            else:
                choices.append((choice[1], choice[0]))
        setattr(
            form,
            field.variablename,
            SelectField(
                field.message,
                default=field.default,
                choices=choices,
                render_kw=render_kw,
            ),
        )

    if isinstance(field, (appform.Directory, appform.File)):
        setattr(
            form,
            field.variablename,
            StringField(field.message, default=field.default, render_kw=render_kw),
        )

    if isinstance(field, appform.Checkbox):
        choices = []
        for choice in field.choices:
            if not isinstance(choice, tuple):
                choices.append((str(choice), str(choice)))
            else:
                choices.append((choice[1], choice[0]))
        setattr(
            form,
            field.variablename,
            SelectMultipleField(
                field.message,
                default=field.default,
                choices=choices,
                render_kw=render_kw,
            ),
        )

    if isinstance(field, appform.Confirm):
        setattr(
            form,
            field.variablename,
            BooleanField(field.message, default=field.default, render_kw=render_kw),
        )

    if isinstance(field, appform.BooleanList):
        fieldid = 0

        class FalseForm(FlaskForm):
            pass

        class TrueForm(FlaskForm):
            pass

        if field.whenfalse:
            for wf in field.whenfalse:
                FalseForm = parse_field(
                    FalseForm,
                    wf,
                    render_kw={"id": f"{field.variablename}_false_{fieldid}"},
                )
                fieldid += 1
        if field.whentrue:
            for wt in field.whentrue:
                TrueForm = parse_field(
                    TrueForm,
                    wt,
                    render_kw={"id": f"{field.variablename}_true_{fieldid}"},
                )
                fieldid += 1
        setattr(
            form,
            field.variablename,
            BooleanField(
                field.message,
                default=field.default,
                render_kw={"onchange": "toggle_questions(this);"},
            ),
        )
        setattr(form, f"{field.variablename}_trueform", FormField(TrueForm, label=""))
        setattr(form, f"{field.variablename}_falseform", FormField(FalseForm, label=""))

    return form


def _form_generator(application, templates, appview):
    if "data" in session:
        data = json.loads(session["data"])
    else:
        data = {}

    class QuestioneryForm(FlaskForm):
        pass

    if len(templates) == 1:
        QuestioneryForm.template = HiddenField(default=templates[0][0])
    elif len(templates) > 1:
        if "default_template" in data:
            default_template = data["default_template"]
        else:
            default_template = None
        QuestioneryForm.template = SelectField(
            "Select template", choices=templates, default=default_template
        )
    questions = appview.mainflow(data)
    while questions:
        field = questions.pop(0)
        QuestioneryForm = parse_field(QuestioneryForm, field)

    if appform.workflows:
        choices = [(None, "--- Select ---")]
        choices.extend([(k, k) for k in appform.workflows.keys()])
        QuestioneryForm.workflow = SelectField("Select workflow", choices=choices)
        appform.workflows = {}

    QuestioneryForm.application = HiddenField("application", default=application)
    QuestioneryForm.submit = SubmitField()

    return QuestioneryForm()


@main_blueprint.route("/")
def home():
    session.pop("data", None)
    return render_template("main/home.html")


@main_blueprint.route("/about/")
def about():
    return render_template("main/about.html")


@main_blueprint.route("/apps/", methods=["GET", "POST"])
def apps():
    class AppForm(FlaskForm):
        application = SelectField("Select application")
        submit = SubmitField()

    appdir = Path(config["apps"]["path"])

    appform = AppForm()
    appform.application.choices = [
        (x.name, x.stem) for x in appdir.iterdir() if x.is_dir()
    ]

    if appform.validate_on_submit():
        application = appform.data["application"]
        templatedir = Path(f"{config['apps']['path']}/{application}/templates/")
        templates = ",".join([template.name for template in templatedir.glob("*.j2")])
        return redirect(
            url_for("main.app", application=application, templates=templates)
        )

    return render_template("main/form.html", form=appform)


@main_blueprint.route("/app/<application>/<templates>", methods=["GET", "POST"])
def app(application, templates):
    templates = [(template, template) for template in templates.split(",")]
    importedlib = fullpath_import(application, "views")

    data = {}
    try:
        with open(
            f"{config['apps']['path']}/{application}/config.yaml", "r"
        ) as ymlfile:
            data.update(yaml.safe_load(ymlfile))
    except FileNotFoundError:
        pass
    session["data"] = json.dumps(data)

    questionsform = _form_generator(application, templates, importedlib)

    if questionsform.validate_on_submit():
        data = json.loads(session["data"])
        data.update(questionsform.data)
        session["data"] = json.dumps(data)
        if "workflow" in questionsform:
            return redirect(
                url_for(
                    "main.renderworkflow",
                    application=application,
                    workflow=questionsform.data["workflow"],
                )
            )
        templatedir = f"{config['apps']['path']}/{application}/templates/"
        template = data.get("template", None) or data.get(
            "default_template", "job_template.j2"
        )
        jinjaenv = Environment(loader=FileSystemLoader(templatedir))
        jinjatemplate = jinjaenv.get_template(template)
        return Response(
            jinjatemplate.render(job=data),
            mimetype="text/x-shellscript",
            headers={"Content-Disposition": f"attachment;filename=jobfile.sh"},
        )

    return render_template(
        "main/form.html", form=questionsform, application=application,
    )


@main_blueprint.route("/workflow/<application>/<workflow>", methods=["GET", "POST"])
def renderworkflow(application, workflow):
    appview = fullpath_import(f"{application}", "views")
    data = json.loads(session["data"])
    try:
        appcontroller = fullpath_import(f"{application}", "controller")

        prefuncs = appcontroller.workflow.prefuncs
        postfuncs = appcontroller.workflow.postfuncs
    except FileNotFoundError:
        prefuncs = {}
        postfuncs = {}

    # If the is a pre_-function in the controller, run that before all
    # questions
    if "" in prefuncs.keys():
        data.update(prefuncs[""](data) or {})

    if workflow in prefuncs.keys():
        data.update(prefuncs[workflow](data) or {})

    appview.appform.questions = deque()

    # "Instantiate" workflow questions
    wfquestions = appview.appform.workflows[workflow]
    wfquestions(data)

    # Ask workflow questions
    appview.appform.workflows = {}
    questionsform = _form_generator(application, [], appview.appform)

    if questionsform.validate_on_submit():
        # FIXME: Same as in apps function.
        # DRY
        templatedir = f"{config['apps']['path']}/{application}/templates/"
        template = data.get("template", None) or data.get(
            "default_template", "job_template.j2"
        )
        jinjaenv = Environment(loader=FileSystemLoader(templatedir))
        jinjatemplate = jinjaenv.get_template(template)
        return Response(
            jinjatemplate.render(job=data),
            mimetype="text/x-shellscript",
            headers={"Content-Disposition": f"attachment;filename=jobfile.sh"},
        )

    # If selected workflow have a post_-function, run that now
    if workflow in postfuncs.keys():
        data.update(postfuncs[workflow](data) or {})
    return render_template(
        "main/form.html", form=questionsform, application=application,
    )
