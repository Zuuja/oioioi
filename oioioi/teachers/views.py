from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.core.mail import EmailMessage
from django.core.urlresolvers import reverse
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.http import require_POST

from oioioi.base.menu import account_menu_registry
from oioioi.base.permissions import (enforce_condition, is_superuser,
                                     make_request_condition, not_anonymous)
from oioioi.base.utils import generate_key
from oioioi.base.utils.confirmation import confirmation_view
from oioioi.contests.menu import contest_admin_menu_registry
from oioioi.contests.models import Contest
from oioioi.contests.utils import contest_exists, is_contest_admin
from oioioi.participants.models import Participant
from oioioi.teachers.controllers import TeacherContestController
from oioioi.teachers.forms import AddTeacherForm
from oioioi.teachers.models import ContestTeacher, RegistrationConfig, Teacher


@make_request_condition
def is_teachers_contest(request):
    return isinstance(request.contest.controller, TeacherContestController)


def is_teachers(contest):
    return isinstance(contest.controller, TeacherContestController)


@make_request_condition
def is_teacher(request):
    return not_anonymous(request) and request.user.has_perm('teachers.teacher')


@make_request_condition
def is_not_teacher(request):
    return not_anonymous(request) and \
           not request.user.has_perm('teachers.teacher')


def send_request_email(request, teacher, message):
    context = {
        'teacher': teacher,
        'accept_link': request.build_absolute_uri(reverse('accept_teacher',
            kwargs={'user_id': teacher.user_id})),
        'message': message.strip(),
    }
    subject = render_to_string('teachers/request_email_subject.txt', context)
    subject = ' '.join(subject.strip().splitlines())
    body = render_to_string('teachers/request_email.txt', context)
    message = EmailMessage(subject, body, settings.SERVER_EMAIL,
            [m[1] for m in settings.MANAGERS],
            headers={'Reply-To': teacher.user.email})
    message.send()


def send_acceptance_email(request, teacher):
    context = {
        'teacher': teacher,
        'new_contest_link': request.build_absolute_uri(
            reverse('oioioiadmin:contests_contest_add')),
    }
    subject = render_to_string('teachers/acceptance_email_subject.txt',
            context)
    subject = ' '.join(subject.strip().splitlines())
    body = render_to_string('teachers/acceptance_email.txt', context)
    teacher.user.email_user(subject, body)


@account_menu_registry.register_decorator(_("Request teacher account"),
    lambda request: reverse('add_teacher'),
    order=100)
@enforce_condition(not_anonymous & is_not_teacher)
def add_teacher_view(request):
    try:
        instance = Teacher.objects.get(user=request.user)
    except Teacher.DoesNotExist:
        instance = None
    if request.method == 'POST':
        form = AddTeacherForm(request.POST, instance=instance)
        if form.is_valid():
            new_instance = form.save(commit=False)
            if not instance:
                new_instance.user = request.user
            new_instance.save()
            send_request_email(request, new_instance,
                    form.cleaned_data['message'])
            return TemplateResponse(request, 'teachers/request_sent.html')
    else:
        form = AddTeacherForm(instance=instance)
    return TemplateResponse(request, 'teachers/request.html', {'form': form})


@enforce_condition(is_superuser)
def accept_teacher_view(request, user_id):
    user = get_object_or_404(User, id=user_id)
    teacher, created = Teacher.objects.get_or_create(user=user)
    if teacher.is_active:
        messages.info(request, _("User already accepted."))
    else:
        choice = confirmation_view(request,
                'teachers/confirm_add_teacher.html', {'teacher': teacher})
        if not isinstance(choice, bool):
            return choice
        if choice:
            teacher.is_active = True
            teacher.save()
            send_acceptance_email(request, teacher)
            messages.success(request, _("Successfully accepted and emailed the"
                " new teacher."))
    return redirect('oioioiadmin:teachers_teacher_changelist')

from oioioi.usergroups.models import UserGroup

@contest_admin_menu_registry.register_decorator(_("Pupils"), lambda request:
        reverse('show_members', kwargs={'contest_id': request.contest.id,
                                      'member_type': 'pupil'}), order=30)
@contest_admin_menu_registry.register_decorator(_("Teachers"), lambda request:
        reverse('show_members', kwargs={'contest_id': request.contest.id,
                                      'member_type': 'teacher'}), order=31)
@enforce_condition(contest_exists & is_teachers_contest & is_contest_admin)
def members_view(request, member_type):
    registration_config, created = RegistrationConfig.objects.get_or_create(
            contest=request.contest)

    if member_type == 'teacher':
        members = User.objects \
            .filter(teacher__contestteacher__contest=request.contest)
        key = registration_config.teacher_key
        is_registration_active = registration_config.is_active_teacher
    elif member_type == 'pupil':
        members = User.objects.filter(participant__contest=request.contest)
        key = registration_config.pupil_key
        is_registration_active = registration_config.is_active_pupil
    else:
        raise Http404

    registration_link = request.build_absolute_uri(
        reverse('teachers_activate_member', kwargs={
            'contest_id': request.contest.id,
            'key': key
        })
    )
    other_contests = Contest.objects \
            .filter(contestteacher__teacher__user=request.user) \
            .exclude(id=request.contest.id)

    return TemplateResponse(request, 'teachers/members.html', {
                'member_type': member_type,
                'members': members,
                'registration_config': registration_config,
                'registration_link': registration_link,
                'other_contests': other_contests,
                'is_registration_active': is_registration_active,
                'usergroups_active': True,
                'has_usergroup': True,
                'usergroups': UserGroup.objects.all()
            })


@enforce_condition(not_anonymous & is_teachers_contest)
def activate_view(request, key):
    registration_config = get_object_or_404(RegistrationConfig,
            contest=request.contest)
    t_active = registration_config.is_active_teacher
    p_active = registration_config.is_active_pupil
    t_key_ok = registration_config.teacher_key == key
    p_key_ok = registration_config.pupil_key == key

    if not (t_key_ok and t_active) and not (p_key_ok and p_active):
        return TemplateResponse(request, 'teachers/activation_error.html', {
            'teacher_key_ok': t_key_ok,
            'pupil_key_ok': p_key_ok,
            'teacher_registration_active': t_active,
            'pupil_registration_active': p_active,
        })

    is_teacher_registration = False
    has_teacher_perm = request.user.has_perm('teachers.teacher')
    if t_key_ok:
        if has_teacher_perm:
            is_teacher_registration = True
        else:
            raise Http404

    if not request.method == 'POST' or 'register_as' not in request.POST:
        return TemplateResponse(request, 'teachers/confirm_join.html',
            {'key': key, 'has_teacher_perm': has_teacher_perm,
             'is_teacher_registration': is_teacher_registration})
    else:
        register_as = request.POST['register_as']
        created = True
        if register_as == 'pupil':
            _p, created = Participant.objects.get_or_create(
                                                contest=request.contest,
                                                user=request.user)
        elif is_teacher_registration and register_as == 'teacher':
            teacher_obj = get_object_or_404(Teacher, user=request.user)
            _ct, created = ContestTeacher.objects.get_or_create(
                                                contest=request.contest,
                                                teacher=teacher_obj)
        else:
            raise SuspiciousOperation

        if not created:
            messages.info(request, _("You are already registered."))
        else:
            messages.info(request, _("Activation successful."))

        return redirect('default_contest_view', contest_id=request.contest.id)


def redirect_to_members(request, member_type='pupil'):
    return redirect(reverse('show_members', kwargs={'contest_id':
        request.contest.id, 'member_type': member_type}))


@require_POST
@enforce_condition(contest_exists & is_teachers_contest & is_contest_admin)
def set_registration_view(request, value, member_type='pupil'):
    registration_config = get_object_or_404(RegistrationConfig,
            contest=request.contest)
    if member_type == 'teacher':
        registration_config.is_active_teacher = value
    elif member_type == 'pupil':
        registration_config.is_active_pupil = value
    else:
        raise Http404

    registration_config.save()
    return redirect_to_members(request, member_type)


@require_POST
@enforce_condition(contest_exists & is_teachers_contest & is_contest_admin)
def regenerate_key_view(request, member_type):
    registration_config = get_object_or_404(RegistrationConfig,
            contest=request.contest)

    if member_type == 'teacher':
        registration_config.teacher_key = generate_key()
    elif member_type == 'pupil':
        registration_config.pupil_key = generate_key()
    else:
        raise Http404

    registration_config.save()

    return redirect_to_members(request, member_type)


@require_POST
@enforce_condition(contest_exists & is_teachers_contest & is_contest_admin)
def delete_members_view(request, member_type):
    if member_type == 'teacher':
        ContestTeacher.objects.filter(contest=request.contest,
                teacher__user_id__in=request.POST.getlist('member')).delete()
    elif member_type == 'pupil':
        Participant.objects.filter(contest=request.contest,
                user_id__in=request.POST.getlist('member')).delete()
    else:
        raise Http404

    return redirect_to_members(request, member_type)


@require_POST
@enforce_condition(contest_exists & is_teachers_contest & is_contest_admin)
def bulk_add_members_view(request, other_contest_id):
    other_contest = get_object_or_404(Contest, id=other_contest_id)
    if not request.user.has_perm('contests.contest_admin', other_contest):
        raise PermissionDenied
    for p in Participant.objects.filter(contest=other_contest):
        Participant.objects.get_or_create(contest=request.contest,
                user=p.user)
    for ct in ContestTeacher.objects.filter(contest=other_contest):
        ContestTeacher.objects.get_or_create(contest=request.contest,
                teacher=ct.teacher)

    messages.info(request, _("Import members completed successfully."))
    return redirect('contest_dashboard', contest_id=request.contest.id)
