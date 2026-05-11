// fork-private — minimal English translations for user-roles UI
const fork = {
  access: {
    role: {
      admin: 'Admin',
      user: 'User',
    },
    forbidden: {
      title: 'Access Denied',
      hint: 'You do not have permission to view this page.',
    },
    project: {
      owner_label: 'Owner',
      owner_unknown: 'Unassigned',
    },
    users: {
      nav: 'Users',
      title: 'User Management',
      empty: 'No users yet. Create your first one below.',
      create: 'Create User',
      create_submit: 'Create',
      column_username: 'Username',
      column_role: 'Role',
      column_created: 'Created',
      column_active: 'Active',
      column_actions: 'Actions',
      delete: 'Delete',
      delete_confirm: 'Delete user "{{name}}"?',
      username_placeholder: '3-32 chars, lowercase / digits / _ / -',
      shared_password_hint: 'All users share the AUTH_PASSWORD environment value. There is no per-user password.',
      created_toast: 'User "{{name}}" created.',
      deleted_toast: 'User "{{name}}" deleted.',
    },
  },
};
export default fork;
