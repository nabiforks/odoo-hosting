# -*- coding: utf-8 -*-
##############################################################################
#
#    Author: Yannick Buron
#    Copyright 2013 Yannick Buron
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################


from openerp import netsvc
from openerp import pooler
from openerp.osv import fields, osv, orm
from openerp.tools.translate import _

import time
from datetime import datetime, timedelta
import subprocess
import paramiko
import execute

import logging
_logger = logging.getLogger(__name__)

class saas_server(osv.osv):
    _name = 'saas.server'
    _inherit = ['saas.log.model']

    _columns = {
        'name': fields.char('Domain name', size=64, required=True),
        'ip': fields.char('IP', size=64, required=True),
        'ssh_port': fields.char('SSH port', size=12, required=True),
        'mysql_passwd': fields.char('MySQL Passwd', size=64),
    }

    def get_vals(self, cr, uid, id, type='', context={}):

        server = self.browse(cr, uid, id, context=context)
        vals ={}

        if 'from_config' not in context:
            config = self.pool.get('ir.model.data').get_object(cr, uid, 'saas', 'saas_settings') 
            vals.update(self.pool.get('saas.config.settings').get_vals(cr, uid, context=context))

        vals.update({
            type + 'server_id': server.id,
            type + 'server_domain': server.name,
            type + 'server_ip': server.ip,
            type + 'server_ssh_port': int(server.ssh_port),
            type + 'server_mysql_passwd': server.mysql_passwd,
            type + 'server_shinken_configfile': '/usr/local/shinken/etc/hosts/' + server.name + '.cfg'
        })
        return vals


    def create(self, cr, uid, vals, context={}):
        res = super(saas_server, self).create(cr, uid, vals, context=context)
        context = self.create_log(cr, uid, res, 'create', context)
        vals = self.get_vals(cr, uid, res, context=context)
        self.deploy(cr, uid, vals, context=context)
        self.end_log(cr, uid, res, context=context)
        return res

    def unlink(self, cr, uid, ids, context={}):
        for server in self.browse(cr, uid, ids, context=context):
            vals = self.get_vals(cr, uid, server.id, context=context)
            self.purge(cr, uid, vals, context=context)
        return super(saas_server, self).unlink(cr, uid, ids, context=context)

    def deploy(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        _logger.info('test %s', vals['shinken_server_domain'])
        if 'shinken_server_domain' in vals:
            ssh, sftp = execute.connect(vals['shinken_fullname'], context=context)
            sftp.put(vals['config_conductor_path'] + '/saas/saas_shinken/res/server-shinken.config', vals['server_shinken_configfile'])
            execute.execute(ssh, ['sed', '-i', '"s/NAME/' + vals['server_domain'] + '/g"', vals['server_shinken_configfile']], context)
            execute.execute(ssh, ['/etc/init.d/shinken', 'reload'], context)
            ssh.close()
            sftp.close()

    def purge(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        if 'shinken_server_domain' in vals:
            ssh, sftp = execute.connect(vals['shinken_fullname'], context=context)
            execute.execute(ssh, ['rm', vals['server_shinken_configfile']], context)
            execute.execute(ssh, ['/etc/init.d/shinken', 'reload'], context)
            ssh.close()
            sftp.close()

class saas_container(osv.osv):
    _name = 'saas.container'
    _inherit = ['saas.log.model']

    _columns = {
        'name': fields.char('Name', size=64, required=True),
        'application_id': fields.many2one('saas.application', 'Application', required=True),
        'image_id': fields.many2one('saas.image', 'Image', required=True),
        'server_id': fields.many2one('saas.server', 'Server', required=True),
        'image_version_id': fields.many2one('saas.image.version', 'Image version', required=True),
        'save_repository_id': fields.many2one('saas.save.repository', 'Save repository'),
        'time_between_save': fields.integer('Minutes between each save'),
        'saverepo_change': fields.integer('Days before saverepo change'),
        'saverepo_expiration': fields.integer('Days before saverepo expiration'),
        'date_next_save': fields.datetime('Next save planned'),
        'save_comment': fields.text('Save Comment'),
        'linked_container_ids': fields.many2many('saas.container', 'saas_container_linked_rel', 'from_id', 'to_id', 'Linked container', domain="[('server_id','=',server_id)]"),
        'port_ids': fields.one2many('saas.container.port', 'container_id', 'Ports'),
        'volume_ids': fields.one2many('saas.container.volume', 'container_id', 'Volumes'),
        'option_ids': fields.one2many('saas.container.option', 'container_id', 'Options'),
        'service_ids': fields.one2many('saas.service', 'container_id', 'Services'),
    }

#########TODO add contraint, image_version doit appartenir � image_id qui doit correspondre � application_id
#########TODO add contraint, a container can only have one linked container of each application type
    def get_vals(self, cr, uid, id, context={}):
        repo_obj = self.pool.get('saas.save.repository')
        vals = {}

        container = self.browse(cr, uid, id, context=context)

        now = datetime.now()
        if not container.save_repository_id:
            repo_ids = repo_obj.search(cr, uid, [('container_name','=',container.name),('container_server','=',container.server_id.name)], context=context)
            if repo_ids:
                self.write(cr, uid, [container.id], {'save_repository_id': repo_ids[0]}, context=context)
                container = self.browse(cr, uid, id, context=context)

        if not container.save_repository_id or datetime.strptime(container.save_repository_id.date_change, "%Y-%m-%d") < now or False:
            repo_vals ={
                'name': now.strftime("%Y-%m-%d") + '_' + container.name + '_' + container.server_id.name,
                'type': 'container',
                'date_change': (now + timedelta(days=container.saverepo_change or container.application_id.container_saverepo_change)).strftime("%Y-%m-%d"),
                'date_expiration': (now + timedelta(days=container.saverepo_expiration or container.application_id.container_saverepo_expiration)).strftime("%Y-%m-%d"),
                'container_name': container.name,
                'container_server': container.server_id.name,
            }
            repo_id = repo_obj.create(cr, uid, repo_vals, context=context)
            self.write(cr, uid, [container.id], {'save_repository_id': repo_id}, context=context)
            container = self.browse(cr, uid, id, context=context)

        if 'from_config' not in context:
            vals.update(self.pool.get('saas.image.version').get_vals(cr, uid, container.image_version_id.id, context=context))
            vals.update(self.pool.get('saas.application').get_vals(cr, uid, container.application_id.id, context=context))
            vals.update(self.pool.get('saas.save.repository').get_vals(cr, uid, container.save_repository_id.id, context=context))
        vals.update(self.pool.get('saas.server').get_vals(cr, uid, container.server_id.id, context=context))


        links = {}
        for link in  container.linked_container_ids:
            links[link.id] = {'id': link.id, 'apptype': link.application_id.type_id.name, 'name': link.name}

        ports = {}
        ssh_port = 22
        for port in container.port_ids:
            ports[port.name] = {'id': port.id, 'name': port.name, 'localport': port.localport, 'hostport': port.hostport}
            if port.name == 'ssh':
                ssh_port = port.hostport

        volumes = {}
        volumes_save = ''
        first = True
        for volume in container.volume_ids:
            volumes[volume.id] = {'id': volume.id, 'name': volume.name, 'hostpath': volume.hostpath, 'readonly': volume.readonly,'nosave': volume.nosave}
            if not volume.nosave:
                volumes_save += (not first and ',' or '') + volume.name
                first = False

        options = {}
        for option in container.application_id.type_id.option_ids:
            if option.type == 'container':
                options[option.name] = {'id': option.id, 'name': option.name, 'value': option.default}
        for option in container.option_ids:
            options[option.name.name] = {'id': option.id, 'name': option.name.name, 'value': option.value}

        unique_name = container.name + '_' + vals['server_domain']
        vals.update({
            'container_id': container.id,
            'container_name': container.name,
            'container_fullname': unique_name,
            'container_ports': ports,
            'container_volumes': volumes,
            'container_volumes_save': volumes_save,
            'container_ssh_port': ssh_port,
            'container_options': options,
            'container_links': links,
            'container_shinken_configfile': '/usr/local/shinken/etc/services/' + unique_name + '.cfg'
        })

        return vals

    def add_links(self, cr, uid, vals, context={}):
        return vals

    def create(self, cr, uid, vals, context={}):
        if ('port_ids' not in vals or not vals['port_ids']) and 'image_version_id' in vals:
            vals['port_ids'] = []
            for port in self.pool.get('saas.image.version').browse(cr, uid, vals['image_version_id'], context=context).image_id.port_ids:
                vals['port_ids'].append((0,0,{'name':port.name,'localport':port.localport}))
        if ('volume_ids' not in vals or not vals['volume_ids']) and 'image_version_id' in vals:
            vals['volume_ids'] = []
            for volume in self.pool.get('saas.image.version').browse(cr, uid, vals['image_version_id'], context=context).image_id.volume_ids:
                vals['volume_ids'].append((0,0,{'name':volume.name,'hostpath':volume.hostpath,'readonly':volume.readonly,'nosave':volume.nosave}))
        vals = self.add_links(cr, uid, vals, context=context)
        res = super(saas_container, self).create(cr, uid, vals, context=context)
        context = self.create_log(cr, uid, res, 'create', context)
        vals = self.get_vals(cr, uid, res, context=context)
        self.deploy(cr, uid, vals, context=context)
        self.end_log(cr, uid, res, context=context)
        return res

    def write(self, cr, uid, ids, vals, context={}):
        version_obj = self.pool.get('saas.image.version')
        save_obj = self.pool.get('saas.save.save')
        if 'image_version_id' in vals:
            for container in self.browse(cr, uid, ids, context=context):
                if container.image_version_id != vals['image_version_id']:
                    context = self.create_log(cr, uid, container.id, 'upgrade version', context)
                    new_version = version_obj.browse(cr, uid, vals['image_version_id'], context=context)
                    context['container_save_comment'] = 'Before upgrade from ' + container.image_version_id.name + ' to ' + new_version.name
                    save_id = self.save(cr, uid, [container.id], context=context)[container.id]
        res = super(saas_container, self).write(cr, uid, ids, vals, context=context)
        if 'image_version_id' in vals:
            for container in self.browse(cr, uid, ids, context=context):
                if container.image_version_id != vals['image_version_id']:
                    self.reinstall(cr, uid, [container.id], context=context)
                    container_vals = self.get_vals(cr, uid, container.id, context=context)
                    save_obj.restore(cr, uid, [save_id], context=context)
                    self.end_log(cr, uid, container.id, context=context)
        return res

    def unlink(self, cr, uid, ids, context={}):
        context['container_save_comment'] = 'Before unlink'
        self.save(cr, uid, ids, context=context)
        for container in self.browse(cr, uid, ids, context=context):
            vals = self.get_vals(cr, uid, container.id, context=context)
            self.purge(cr, uid, vals, context=context)
        return super(saas_container, self).unlink(cr, uid, ids, context=context)

    def save(self, cr, uid, ids, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        save_obj = self.pool.get('saas.save.save')

        res = {}
        for container in self.browse(cr, uid, ids, context=context):
            context = self.create_log(cr, uid, container.id, 'save', context)
            vals = self.get_vals(cr, uid, container.id, context=context)
            if not 'bup_server_domain' in vals:
                execute.log('The bup isnt configured in conf, skipping save container', context)
                return
            save_vals = {
                'name': vals['now_bup'] + '_' + vals['container_fullname'],
                'repo_id': vals['saverepo_id'],
                'comment': 'container_save_comment' in context and context['container_save_comment'] or container.save_comment or 'Manual',
                'now_bup': vals['now_bup'],
                'container_id': vals['container_id'],
                'container_volumes_comma': vals['container_volumes_save'],
                'container_app': vals['app_code'],
                'container_img': vals['image_name'],
                'container_img_version': vals['image_version_name'],
                'container_ports': str(vals['container_ports']),
                'container_volumes': str(vals['container_volumes']),
                'container_options': str(vals['container_options']),
            }
            res[container.id] = save_obj.create(cr, uid, save_vals, context=context)
            next = (datetime.now() + timedelta(minutes=container.time_between_save or container.application_id.container_time_between_save)).strftime("%Y-%m-%d %H:%M:%S")
            self.write(cr, uid, [container.id], {'save_comment': False, 'date_next_save': next}, context=context)
            self.end_log(cr, uid, container.id, context=context)
        return res


    def reset_key(self, cr, uid, ids, context={}):
        for container in self.browse(cr, uid, ids, context=context):
            vals = self.get_vals(cr, uid, container.id, context=context)
            self.deploy_key(cr, uid, vals, context=context)

    def reset_shinken(self, cr, uid, ids, context={}):
        for container in self.browse(cr, uid, ids, context=context):
            vals = self.get_vals(cr, uid, container.id, context=context)
            self.deploy_shinken(cr, uid, vals, context=context)

    def deploy_post(self, cr, uid, vals, context=None):
        return

    def deploy(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        #container = self.browse(cr, uid, id, context=context)

        ssh, sftp = execute.connect(vals['server_domain'], vals['server_ssh_port'], 'root', context)

        cmd = ['sudo','docker', 'run', '-d']
        nextport = STARTPORT
        for key, port in vals['container_ports'].iteritems():
            if not port['hostport']:
                while not port['hostport'] and nextport != ENDPORT:
                    port_ids = self.pool.get('saas.container.port').search(cr, uid, [('hostport','=',nextport),('container_id.server_id','=',vals['server_id'])], context=context)
                    if not port_ids and not execute.execute(ssh, ['netstat', '-an', '|', 'grep', str(nextport)], context):
                        self.pool.get('saas.container.port').write(cr, uid, [port['id']], {'hostport': nextport}, context=context)
                        port['hostport'] = nextport
                        if port['name'] == 'ssh':
                            vals['container_ssh_port'] = nextport
                    nextport += 1
                    _logger.info('nextport %s', nextport)
            _logger.info('server_id %s, hostport %s, localport %s', vals['server_ip'], port['hostport'], port['localport'])
            cmd.extend(['-p', vals['server_ip'] + ':' + str(port['hostport']) + ':' + port['localport']])
        for key, volume in vals['container_volumes'].iteritems():
            if volume['hostpath']:
                arg =  volume['hostpath'] + ':' + volume['name']
                if volume['readonly']:
                    arg += ':ro'
                cmd.extend(['-v', arg])
        for key, link in vals['container_links'].iteritems():
            cmd.extend(['--link', link['name'] + ':' + link['name']])
        cmd.extend(['-v', '/opt/keys/' + vals['container_fullname'] + '.pub:/opt/authorized_keys', '--name', vals['container_name'], vals['image_version_fullname']])

        #Deploy key now, otherwise the container will be angry to not find the key. We can't before because vals['container_ssh_port'] may not be set
        self.deploy_key(cr, uid, vals, context=context)

        #Run container
        execute.execute(ssh, cmd, context)

        time.sleep(5)

        self.deploy_post(cr, uid, vals, context)

        execute.execute(ssh, ['sudo', 'docker', 'restart', vals['container_name']], context)

        ssh.close()
        sftp.close()

        if not 'shinken_server_domain' in vals:
            execute.log('The shinken isnt configured in conf, skipping placing dummy save in shinken', context)
        else:
            ssh, sftp = execute.connect(vals['shinken_fullname'], context=context)
            execute.execute(ssh, ['mkdir', '-p', '/opt/control-bup/restore/' + vals['container_fullname'] + '/latest'], context)
            execute.execute(ssh, ['echo "' + vals['now_date'] + '" >> /opt/control-bup/restore/' + vals['container_fullname'] + '/latest/backup-date'], context)
            execute.execute(ssh, ['chown', '-R', 'shinken:shinken', '/opt/control-bup'], context)
            ssh.close()
            sftp.close()
            self.deploy_shinken(cr, uid, vals, context=context)

        return

    def purge(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
#        container = self.browse(cr, uid, id, context=context)
        ssh, sftp = execute.connect(vals['server_domain'], vals['server_ssh_port'], 'root', context)
        execute.execute(ssh, ['sudo','docker', 'stop', vals['container_name']], context)
        execute.execute(ssh, ['sudo','docker', 'rm', vals['container_name']], context)
        ssh.close()
        sftp.close()

        self.purge_shinken(cr, uid, vals, context=context)
        self.purge_key(cr, uid, vals, context=context)
        return

    def stop(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        ssh, sftp = execute.connect(vals['server_domain'], vals['server_ssh_port'], 'root', context)
        execute.execute(ssh, ['docker', 'stop', vals['container_name']], context)
        ssh.close()
        sftp.close()

    def start(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        ssh, sftp = execute.connect(vals['server_domain'], vals['server_ssh_port'], 'root', context)
        execute.execute(ssh, ['docker', 'start', vals['container_name']], context)
        ssh.close()
        sftp.close()

    def restart(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        ssh, sftp = execute.connect(vals['server_domain'], vals['server_ssh_port'], 'root', context)
        execute.execute(ssh, ['docker', 'restart', vals['container_name']], context)
        ssh.close()
        sftp.close()


    def deploy_shinken(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        if not 'shinken_server_domain' in vals:
            execute.log('The shinken isnt configured in conf, skipping deploy container shinken', context)
            return
        self.purge_shinken(cr, uid, vals, context=context)
        ssh, sftp = execute.connect(vals['shinken_fullname'], context=context)
        sftp.put(vals['config_conductor_path'] + '/saas/saas_shinken/res/container-shinken.config', vals['container_shinken_configfile'])
        execute.execute(ssh, ['sed', '-i', '"s/UNIQUE_NAME/' + vals['container_fullname'] + '/g"', vals['container_shinken_configfile']], context)
        execute.execute(ssh, ['/etc/init.d/shinken', 'reload'], context)
        ssh.close()
        sftp.close()

    def purge_shinken(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        if not 'shinken_server_domain' in vals:
            execute.log('The shinken isnt configured in conf, skipping purge container shinken', context)
            return
        ssh, sftp = execute.connect(vals['shinken_fullname'], context=context)
        execute.execute(ssh, ['rm', vals['container_shinken_configfile']], context)
        execute.execute(ssh, ['/etc/init.d/shinken', 'reload'], context)
        ssh.close()
        sftp.close()

    def deploy_key(self, cr, uid, vals, context={}):
        context.update({'saas-self': self, 'saas-cr': cr, 'saas-uid': uid})
        self.purge_key(cr, uid, vals, context=context)
        execute.execute_local(['ssh-keygen', '-t', 'rsa', '-C', 'yannick.buron@gmail.com', '-f', vals['config_home_directory'] + '/keys/' + vals['container_fullname'], '-N', ''], context)
        execute.execute_write_file(vals['config_home_directory'] + '/.ssh/config', 'Host ' + vals['container_fullname'], context)
        execute.execute_write_file(vals['config_home_directory'] + '/.ssh/config', '\n  HostName ' + vals['server_domain'], context)
        execute.execute_write_file(vals['config_home_directory'] + '/.ssh/config', '\n  Port ' + str(vals['container_ssh_port']), context)
        execute.execute_write_file(vals['config_home_directory'] + '/.ssh/config', '\n  User root', context)
        execute.execute_write_file(vals['config_home_directory'] + '/.ssh/config', '\n  IdentityFile ~/keys/' + vals['container_fullname'], context)
        execute.execute_write_file(vals['config_home_directory'] + '/.ssh/config', '\n#END ' + vals['container_fullname'] + '\n', context)
        ssh, sftp = execute.connect(vals['server_domain'], vals['server_ssh_port'], 'root', context)
        sftp.put(vals['config_home_directory'] + '/keys/' + vals['container_fullname'] + '.pub', '/opt/keys/' + vals['container_fullname'] + '.pub')
        ssh.close()
        sftp.close()
        if vals['container_id'] == vals['bup_id']:
            context['key_already_reset'] = True
            self.pool.get('saas.config.settings').reset_bup_key(cr, uid, [], context=context)
        self.restart(cr, uid, vals, context=context)

    def purge_key(self, cr, uid, vals, context={}):
        ssh, sftp = execute.connect('localhost', 22, 'saas-conductor', context)
        execute.execute(ssh, ['sed', '-i', "'/Host " + vals['container_fullname'] + "/,/END " + vals['container_fullname'] + "/d'", vals['config_home_directory'] + '/.ssh/config'], context)
        ssh.close()
        sftp.close()
        execute.execute_local(['rm', '-rf', vals['config_home_directory'] + '/keys/' + vals['container_fullname']], context)
        execute.execute_local(['rm', '-rf', vals['config_home_directory'] + '/keys/' + vals['container_fullname'] + '.pub'], context)
        ssh, sftp = execute.connect(vals['server_domain'], vals['server_ssh_port'], 'root', context)
        execute.execute(ssh, ['rm', '-rf', '/opt/keys/' + vals['container_fullname'] + '*'], context)
        ssh.close()
        sftp.close()




class saas_container_port(osv.osv):
    _name = 'saas.container.port'

    _columns = {
        'container_id': fields.many2one('saas.container', 'Container', ondelete="cascade", required=True),
        'name': fields.char('Name', size=64, required=True),
        'localport': fields.char('Local port', size=12, required=True),
        'hostport': fields.char('Host port', size=12),
    }

class saas_container_volume(osv.osv):
    _name = 'saas.container.volume'

    _columns = {
        'container_id': fields.many2one('saas.container', 'Container', ondelete="cascade", required=True),
        'name': fields.char('Path', size=128, required=True),
        'hostpath': fields.char('Host path', size=128),
        'readonly': fields.boolean('Readonly?'),
        'nosave': fields.boolean('No save?'),
    }

class saas_container_option(osv.osv):
    _name = 'saas.container.option'

    _columns = {
        'container_id': fields.many2one('saas.container', 'Container', ondelete="cascade", required=True),
        'name': fields.many2one('saas.application.type.option', 'Option', required=True),
        'value': fields.text('Value'),
    }

