#cloud-config                                                                                                                                                                                                [40/92]
groups:
- docker
users:
- name: ${useros}
  groups:
    - sudo
    - docker
  shell: /bin/bash
  sudo: ['ALL=(ALL) NOPASSWD:ALL']
  ssh-authorized-keys:
    - ${adm_pub_key}
- name: gitlab-runner
  groups: docker

package_upgrade: false
package_update: true

