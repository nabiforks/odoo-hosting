# -*- coding: utf-8 -*-
# Copyright 2015 Clouder SASU
# License LGPL-3.0 or later (http://gnu.org/licenses/lgpl.html).


try:
    from odoo import models, fields
except ImportError:
    from openerp import models, fields


class ClouderApplicationLink(models.Model):
    """
    Define the application.link object, used to know which others applications
    can be link to this application.
    """

    _name = 'clouder.application.link'

    _inherit = ['clouder.template.one2many']

    _template_parent_model = 'clouder.application'
    _template_parent_many2one = 'application_id'
    _template_fields = ['required', 'auto', 'make_link', 'service', 'base']

    application_id = fields.Many2one('clouder.application', 'Application',
                                     ondelete="cascade", required=False)
    template_id = fields.Many2one(
        'clouder.application.template', 'Template',
        ondelete="cascade", required=False)
    name = fields.Many2one('clouder.application.tag', 'Application',
                           ondelete="cascade", required=True)
    required = fields.Boolean('Required?')
    auto = fields.Boolean('Auto?')
    make_link = fields.Boolean('Make docker link?')
    service = fields.Boolean('Service?')
    base = fields.Boolean('Base?')
    next = fields.Many2one('clouder.service', 'Next')

    _sql_constraints = [
        ('name_uniq', 'unique(application_id,template_id,name)',
         'Links must be unique per application!'),
    ]
