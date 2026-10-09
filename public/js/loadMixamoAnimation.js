import * as THREE from 'three';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { mixamoVRMRigMap } from './mixamoVRMRigMap.js';

const fbxLoader = new FBXLoader();

/**
 * Load a Mixamo FBX animation and retarget it onto a VRM.
 *
 * The FBX skeleton uses different bone names and a different rest pose than the
 * VRM, so every rotation track has to be rebuilt: with `R` the Mixamo bone's
 * rest world rotation and `P` its parent's, each key `q` becomes `P * q * R⁻¹`.
 * VRM 0.x models additionally face the other way, so x and z get negated.
 *
 * Same method as the official three-vrm "humanoidAnimation" example.
 *
 * @param {string} url  path to the .fbx (Mixamo, FBX binary, without skin)
 * @param {import('@pixiv/three-vrm').VRM} vrm
 * @returns {Promise<THREE.AnimationClip>} a clip for an AnimationMixer on vrm.scene
 */
export async function loadMixamoAnimation(url, vrm) {
  const asset = await fbxLoader.loadAsync(url);

  const clip = THREE.AnimationClip.findByName(asset.animations, 'mixamo.com');
  if (!clip) {
    throw new Error(`${url}: no "mixamo.com" clip inside this FBX`);
  }

  const tracks = [];

  const restRotationInverse = new THREE.Quaternion();
  const parentRestWorldRotation = new THREE.Quaternion();
  const _quatA = new THREE.Quaternion();
  const _vec3 = new THREE.Vector3();

  // Scale the hips travel from Mixamo's centimetre-ish rig to this avatar.
  const motionHipsHeight = asset.getObjectByName('mixamorigHips').position.y;
  const vrmHipsY = vrm.humanoid
    .getNormalizedBoneNode('hips')
    .getWorldPosition(_vec3).y;
  const vrmRootY = vrm.scene.getWorldPosition(_vec3).y;
  const vrmHipsHeight = Math.abs(vrmHipsY - vrmRootY);
  const hipsPositionScale = vrmHipsHeight / motionHipsHeight;

  const isVRM0 = vrm.meta?.metaVersion === '0';

  for (const track of clip.tracks) {
    const [mixamoRigName, propertyName] = track.name.split('.');
    const vrmBoneName = mixamoVRMRigMap[mixamoRigName];
    const vrmNodeName = vrm.humanoid.getNormalizedBoneNode(vrmBoneName)?.name;
    const mixamoRigNode = asset.getObjectByName(mixamoRigName);

    if (!vrmNodeName || !mixamoRigNode) continue; // bone this VRM does not have

    mixamoRigNode.getWorldQuaternion(restRotationInverse).invert();
    mixamoRigNode.parent.getWorldQuaternion(parentRestWorldRotation);

    if (track instanceof THREE.QuaternionKeyframeTrack) {
      for (let i = 0; i < track.values.length; i += 4) {
        _quatA.fromArray(track.values, i);
        _quatA.premultiply(parentRestWorldRotation).multiply(restRotationInverse);
        _quatA.toArray(track.values, i);
      }

      tracks.push(
        new THREE.QuaternionKeyframeTrack(
          `${vrmNodeName}.${propertyName}`,
          track.times,
          // VRM 0.x looks down -Z: flip x and z (indices 0 and 2 of each quat).
          track.values.map((v, i) => (isVRM0 && i % 2 === 0 ? -v : v)),
        ),
      );
    } else if (track instanceof THREE.VectorKeyframeTrack) {
      tracks.push(
        new THREE.VectorKeyframeTrack(
          `${vrmNodeName}.${propertyName}`,
          track.times,
          track.values.map(
            (v, i) => (isVRM0 && i % 3 !== 1 ? -v : v) * hipsPositionScale,
          ),
        ),
      );
    }
  }

  return new THREE.AnimationClip(url, clip.duration, tracks);
}
