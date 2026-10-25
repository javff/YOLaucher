--混沌の黒魔術師
--Dark Magician of Chaos (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "When this card is Normal or Special Summoned, you can select and add 1
--Spell Card from your Graveyard to your hand. Monsters this card destroys by battle are removed from play instead of going
--to the Graveyard. If this face-up card is destroyed or removed from the field, it is removed from play."
--Diferencias con CardScripts (errata moderna): (1) el efecto de recuperar la Magia es un 'When ... Summoned' (SINGLE
--TRIGGER_O al tener exito la Invocacion Normal o Especial, con objetivo), no 'During the End Phase, if this card was
--Normal or Special Summoned this turn' (fuera la marca del turno, el efecto de la End Phase y el 'once per turn');
--(2) los monstruos que destruye en combate se destierran EN LUGAR de ir al Cementerio (EFFECT_BATTLE_DESTROY_REDIRECT,
--como Lesser Fiend), no se destierran DESPUES de ir (fuera el trigger de EVENT_BATTLED: en 2008 no pisan el
--Cementerio y no disparan sus efectos de 'sent to the Graveyard'). (3) igual: si boca arriba deja el campo, se destierra.
local s,id=GetID()
function s.initial_effect(c)
	--add 1 Spell from the GY when Normal/Special Summoned
	local e1=Effect.CreateEffect(c)
	e1:SetDescription(aux.Stringid(id,0))
	e1:SetCategory(CATEGORY_TOHAND)
	e1:SetType(EFFECT_TYPE_SINGLE+EFFECT_TYPE_TRIGGER_O)
	e1:SetProperty(EFFECT_FLAG_CARD_TARGET)
	e1:SetCode(EVENT_SUMMON_SUCCESS)
	e1:SetTarget(s.thtg)
	e1:SetOperation(s.thop)
	c:RegisterEffect(e1)
	local e2=e1:Clone()
	e2:SetCode(EVENT_SPSUMMON_SUCCESS)
	c:RegisterEffect(e2)
	--monsters destroyed by battle with this card are banished instead
	local e3=Effect.CreateEffect(c)
	e3:SetType(EFFECT_TYPE_SINGLE)
	e3:SetProperty(EFFECT_FLAG_SINGLE_RANGE)
	e3:SetRange(LOCATION_MZONE)
	e3:SetCode(EFFECT_BATTLE_DESTROY_REDIRECT)
	e3:SetValue(LOCATION_REMOVED)
	c:RegisterEffect(e3)
	--redirect
	local e4=Effect.CreateEffect(c)
	e4:SetType(EFFECT_TYPE_SINGLE)
	e4:SetCode(EFFECT_LEAVE_FIELD_REDIRECT)
	e4:SetProperty(EFFECT_FLAG_CANNOT_DISABLE)
	e4:SetCondition(s.recon)
	e4:SetValue(LOCATION_REMOVED)
	c:RegisterEffect(e4)
end
function s.thfilter(c)
	return c:IsSpell() and c:IsAbleToHand()
end
function s.thtg(e,tp,eg,ep,ev,re,r,rp,chk,chkc)
	if chkc then return chkc:IsLocation(LOCATION_GRAVE) and chkc:IsControler(tp) and s.thfilter(chkc) end
	if chk==0 then return Duel.IsExistingTarget(s.thfilter,tp,LOCATION_GRAVE,0,1,nil) end
	Duel.Hint(HINT_SELECTMSG,tp,HINTMSG_ATOHAND)
	local g=Duel.SelectTarget(tp,s.thfilter,tp,LOCATION_GRAVE,0,1,1,nil)
	Duel.SetOperationInfo(0,CATEGORY_TOHAND,g,1,0,0)
end
function s.thop(e,tp,eg,ep,ev,re,r,rp)
	local tc=Duel.GetFirstTarget()
	if tc and tc:IsRelateToEffect(e) then
		Duel.SendtoHand(tc,nil,REASON_EFFECT)
	end
end
function s.recon(e)
	return e:GetHandler():IsFaceup()
end
