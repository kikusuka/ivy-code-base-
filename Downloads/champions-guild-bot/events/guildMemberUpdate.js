const { db } = require('../utils/db');

module.exports = {
  name: 'guildMemberUpdate',
  async execute(oldMember, newMember) {
    const userId = newMember.user.id;

    // Track username changes
    const oldTag = oldMember.user.tag;
    const newTag = newMember.user.tag;
    if (oldTag !== newTag) {
      const user = db.getUser(userId) || {};
      const history = user.usernameHistory || [];
      history.push({ name: newTag, changedAt: new Date().toISOString() });
      db.setUser(userId, { usernameHistory: history, tag: newTag });
    }

    // Track role changes
    const addedRoles = newMember.roles.cache.filter(r => !oldMember.roles.cache.has(r.id));
    const removedRoles = oldMember.roles.cache.filter(r => !newMember.roles.cache.has(r.id));

    if (addedRoles.size > 0 || removedRoles.size > 0) {
      const user = db.getUser(userId) || {};
      const roleHistory = user.roleHistory || [];

      addedRoles.forEach(role => {
        if (role.id === newMember.guild.id) return; // skip @everyone
        roleHistory.push({ action: '+', role: role.name, changedAt: new Date().toISOString() });
      });
      removedRoles.forEach(role => {
        if (role.id === newMember.guild.id) return;
        roleHistory.push({ action: '−', role: role.name, changedAt: new Date().toISOString() });
      });

      // Keep last 50 history entries
      db.setUser(userId, {
        roleHistory: roleHistory.slice(-50),
        roles: newMember.roles.cache
          .filter(r => r.id !== newMember.guild.id)
          .map(r => r.name),
      });
    }
  },
};
